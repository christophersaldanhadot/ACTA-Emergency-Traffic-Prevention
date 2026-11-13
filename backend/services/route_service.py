import asyncio
import hashlib
import json
import math
import os
import httpx
from dotenv import load_dotenv

from services.polyline_decoder import decode_polyline

load_dotenv()

# Set ACTA_MOCK_ROUTES=1 to run the dashboard end-to-end without a live
# GOOGLE_MAPS_API_KEY (e.g. offline demos/screenshots). The mock route
# is clearly labeled ("source": "mock") wherever it's returned -- it is
# NEVER used silently just because a request happens to fail, only when
# this flag is explicitly set.
MOCK_ROUTES_ENABLED = os.getenv("ACTA_MOCK_ROUTES", "").lower() in ("1", "true", "yes")


def _mock_route(origin: str, destination: str, routing_mode: str = "fastest") -> dict:
    """A small, fixed, clearly-labeled synthetic route (real Kengeri ->
    RR Nagar corridor coordinates, ~2.7km, both endpoints well inside
    the Bengaluru metro bias radius) for offline demos. It does NOT
    reflect the actual origin/destination text -- that's the trade-off
    of not calling a real geocoder without an API key."""
    return {
        "distance_meters": 2700,
        "duration_seconds": 480,  # ~8 min free-flow
        "duration_in_traffic_seconds": 1020,  # ~17 min with mock live traffic
        "polyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB",
        "traffic_aware": True,
        "routing_mode": routing_mode,
        "source": "mock",
    }


class RouteService:
    # Bengaluru metropolitan bias, per project requirements: a ~50km
    # circular region centered on central Bangalore. Used both to bias
    # (not hard-restrict) Google's own address resolution inside
    # computeRoutes, and as a hard post-hoc sanity check on the
    # resolved route endpoints below -- so a fuzzy/ambiguous input like
    # "sda hospital" can no longer silently resolve to a same-named
    # place elsewhere in India (or the world) and produce a
    # cross-country route before it ever reaches Overpass.
    BLR_CENTER_LAT = 12.9716
    BLR_CENTER_LNG = 77.5946
    BLR_BIAS_RADIUS_M = 50000.0

    @staticmethod
    def _haversine_m(lat1, lng1, lat2, lng2):
        """Great-circle distance in metres between two lat/lng points."""
        R = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lng2 - lng1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        self.url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        # Only used by tests, to inject a deterministic httpx.MockTransport
        # instead of hitting the real network.
        self.transport = transport

    def _waypoint_payload(self, value: str, place_id: str | None) -> dict:
        """Build a Routes API Waypoint object. When the frontend's
        Places Autocomplete resolved a specific place, `place_id` is
        passed and used directly (Google's own guidance: place IDs are
        "more efficient and accurate" than address strings, and avoid
        re-geocoding an already-disambiguated selection). Falls back to
        the free-text `address` string only when no place_id is given
        (e.g. a manually-typed address that was never run through
        Autocomplete)."""
        if place_id:
            return {"placeId": place_id}
        return {"address": value}

    async def _google_route(
        self,
        origin: str,
        destination: str,
        routing_mode: str = "fastest",
        origin_place_id: str | None = None,
        destination_place_id: str | None = None,
        
    ):
        """
        routing_mode:
          "fastest"  -> request alternative routes (computeAlternativeRoutes),
                        then explicitly pick whichever returned route has the
                        lowest `duration` (Routes API's traffic-aware ETA,
                        equivalent to the legacy Directions API's
                        `duration_in_traffic`).
          "shortest" -> request alternative routes, then explicitly pick
                        whichever returned route has the lowest
                        `distanceMeters`.

        `origin`/`destination` are the human-readable text (always
        required, used for logging/error messages/mock mode).
        `origin_place_id`/`destination_place_id` are optional Google
        Place IDs -- when the dashboard's Places Autocomplete resolved
        a specific place, pass its place_id here so Routes API skips
        re-geocoding the (already disambiguated) text.

        Previously this only ever requested a single route
        (`computeAlternativeRoutes: False`) and relied on
        `routingPreference` alone to differentiate the two modes, which
        meant both modes could silently return the same single path (same
        distance, near-identical/inverted ETAs) whenever Google's routing
        preference didn't actually change the chosen route. Requesting
        alternatives and explicitly selecting per-mode fixes that.
        """

        if not self.api_key:
            if MOCK_ROUTES_ENABLED:
                return _mock_route(origin, destination, routing_mode)
            raise ValueError(
                "GOOGLE_MAPS_API_KEY not found. Set it in the project's .env "
                "file, or set ACTA_MOCK_ROUTES=1 to run against a mock route "
                "for offline demos."
            )

        if routing_mode not in ("fastest", "shortest"):
            raise ValueError(f"Unknown routing_mode '{routing_mode}' (expected 'fastest' or 'shortest').")

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": (
                "routes.duration,"
                "routes.staticDuration,"
                "routes.distanceMeters,"
                "routes.polyline.encodedPolyline,"
                # Needed to validate WHERE the fuzzy origin/destination
                # strings actually resolved to, so we can reject a
                # geocode that landed outside the metro area instead of
                # silently handing a cross-country polyline to Overpass.
                "geocodingResults"
            ),
        }

        payload = {
            "origin": self._waypoint_payload(origin, origin_place_id),
            "destination": self._waypoint_payload(destination, destination_place_id),
            # Hardcoded to car/passenger-vehicle routing -- never bike,
            # walk, or transit. Do not make this configurable from the
            # request without also re-checking the SUMO scenario builder,
            # which assumes a single passenger-car corridor.
            "travelMode": "DRIVE",
            # TRAFFIC_AWARE_OPTIMAL asks Google's live traffic model for
            # the best-available current-conditions ETA (the Routes API's
            # equivalent of the legacy Directions API's
            # `duration_in_traffic`); the plain `duration` field below
            # becomes that traffic-aware number, while `staticDuration`
            # is the traffic-free baseline for comparison.
            #
            # "shortest" mode uses TRAFFIC_UNAWARE instead: the Routes
            # API has no first-class "minimize distance" objective, so
            # this is the closest available proxy -- without a live
            # traffic-cost term Google's routing falls back to a static,
            # largely distance-driven shortest path.
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            # Ask for alternative paths (not just Google's single default
            # choice) so that "fastest" and "shortest" have real distinct
            # candidates to pick from below, instead of both modes
            # collapsing onto whatever single route Google happened to
            # return first.
            "computeAlternativeRoutes": True,
            # Bias fuzzy address-string geocoding (e.g. "sda hospital")
            # toward India, per Routes API's supported region-biasing
            # mechanism (regionCode, a ccTLD -- there is no per-waypoint
            # circular locationBias field on computeRoutes; that's a
            # Places API concept, not available here). This is a bias,
            # not a hard restriction -- Google can still resolve an
            # address outside India if nothing plausible exists inside
            # it, which is why the endpoint distance check below exists
            # as the actual enforcement point.
            "regionCode": "IN",
        }

        response = None
        last_exc = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as client:
                    response = await client.post(self.url, headers=headers, json=payload)
            except httpx.RequestError as e:
                last_exc = e
                print(f"[ROUTES] Google attempt {attempt + 1} failed: {e!r}")
                response = None
            else:
                if response.status_code in (429, 500, 502, 503, 504):
                    print(f"[ROUTES] Google attempt {attempt + 1} HTTP {response.status_code}")
                    last_exc = None
                else:
                    break
            if self.transport is None and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
        if response is None:
            raise Exception(f"Could not reach Google Routes API: {last_exc!r}")

        if response.status_code == 403:
            raise Exception(
                "Google Routes API returned 403 Forbidden -- check that "
                "GOOGLE_MAPS_API_KEY is valid and the Routes API is "
                f"enabled for it. Response: {response.text}"
            )
        if response.status_code == 429:
            raise Exception("Google Routes API rate limit/quota exceeded (429).")
        if response.status_code != 200:
            raise Exception(f"Google Routes API returned {response.status_code}: {response.text}")

        try:
            data = response.json()
        except ValueError as e:
            raise Exception(f"Google Routes API returned malformed JSON: {e}")

        if "routes" not in data or len(data["routes"]) == 0:
            raise Exception(
                "Google Routes API returned no routes for this origin/destination pair "
                "(check the addresses are valid and reachable by DRIVE)."
            )

        routes = data["routes"]
        print(f"[{routing_mode}] {len(routes)} route(s) returned, distances={[r['distanceMeters'] for r in routes]}")

        # `duration`/`staticDuration` are protobuf Duration values
        # serialized as strings, e.g. "1234s" or "1234.500s" (fractional
        # seconds are legal and DO occur in real responses) -- int() on
        # a value with a decimal point raises ValueError, so parse as
        # float first.
        def _parse_duration(value):
            if value is None:
                return None
            return int(round(float(value.rstrip("s"))))

        # Explicitly select the right candidate among all returned
        # alternatives, instead of trusting routes[0] (Google's default
        # ordering isn't guaranteed to match either objective).
        if routing_mode == "fastest":
            # Lowest traffic-aware duration wins. Missing `duration` is
            # treated as worst-case (+inf) so it's never picked over a
            # route that actually reports one.
            route = min(
                routes,
                key=lambda r: _parse_duration(r.get("duration")) if r.get("duration") is not None else float("inf"),
            )
        else:  # "shortest"
            route = min(routes, key=lambda r: r["distanceMeters"])

        duration_in_traffic_seconds = _parse_duration(route["duration"])
        static_duration_seconds = _parse_duration(route.get("staticDuration"))
        encoded_polyline = route["polyline"]["encodedPolyline"]

        # Hard enforcement of the metro-area requirement: `regionCode`
        # above only *biases* Google's geocoding, it doesn't guarantee
        # a fuzzy/ambiguous string (e.g. "sda hospital", which matches
        # same-named places all over India) resolves locally. The
        # ground truth of where the route actually starts/ends is the
        # polyline itself -- its first and last decoded points are
        # exactly where "origin" and "destination" were geocoded to.
        # If either endpoint falls outside the Bengaluru metro bias
        # radius, reject the route here, before its polyline is ever
        # handed to Overpass for signal matching -- that's what turns
        # a silent cross-country route into a clear, actionable error.
        coords = decode_polyline(encoded_polyline)
        if not coords:
            raise Exception("Google Routes API returned an empty/undecodable polyline.")

        start, end = coords[0], coords[-1]
        start_dist_m = self._haversine_m(
            self.BLR_CENTER_LAT, self.BLR_CENTER_LNG, start["lat"], start["lng"]
        )
        end_dist_m = self._haversine_m(
            self.BLR_CENTER_LAT, self.BLR_CENTER_LNG, end["lat"], end["lng"]
        )
        if start_dist_m > self.BLR_BIAS_RADIUS_M or end_dist_m > self.BLR_BIAS_RADIUS_M:
            offender, dist_m = (
                ("origin", start_dist_m) if start_dist_m > self.BLR_BIAS_RADIUS_M else ("destination", end_dist_m)
            )
            raise Exception(
                f"Resolved {offender} ('{origin if offender == 'origin' else destination}') is "
                f"{dist_m / 1000:.1f} km from central Bangalore, outside the "
                f"{self.BLR_BIAS_RADIUS_M / 1000:.0f} km metro service area. This usually means "
                "the address was too ambiguous and Google geocoded it to a same-named place "
                "elsewhere. Try a more specific address (add area/landmark/pincode)."
            )

        return {
            "distance_meters": route["distanceMeters"],
            # Kept for backward compatibility with existing callers --
            # now traffic-aware, same value as duration_in_traffic_seconds.
            "duration_seconds": duration_in_traffic_seconds,
            "duration_in_traffic_seconds": duration_in_traffic_seconds,
            "static_duration_seconds": static_duration_seconds,
            "polyline": encoded_polyline,
            "traffic_aware": True,
            "routing_mode": routing_mode,
            "source": "google_routes_api",
        }


    # ------------------------------------------------------------------
    # Resilient wrapper: Google -> disk cache -> OSRM/Nominatim fallback
    # ------------------------------------------------------------------
    ROUTE_CACHE_DIR = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "cache", "routes")
    )

    def _route_cache_path(self, origin, destination, routing_mode, o_pid, d_pid):
        key = json.dumps(
            [(o_pid or origin).strip().lower(), (d_pid or destination).strip().lower(), routing_mode]
        )
        return os.path.join(self.ROUTE_CACHE_DIR, hashlib.sha1(key.encode()).hexdigest() + ".json")

    async def _osrm_route(self, origin, destination, routing_mode):
        headers = {"User-Agent": "ACTA-research-project/1.0"}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            async def geocode(q):
                r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": q, "format": "json", "limit": 1, "countrycodes": "in",
                        "viewbox": "77.30,13.20,77.90,12.70", "bounded": 0,
                    },
                )
                r.raise_for_status()
                j = r.json()
                if not j:
                    # retry with a shorter query (drop trailing address parts)
                    short = ", ".join(q.split(",")[:3])
                    r = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params={"q": short + ", Bengaluru", "format": "json", "limit": 1},
                    )
                    r.raise_for_status()
                    j = r.json()
                if not j:
                    raise Exception(f"Could not geocode '{q}'")
                return float(j[0]["lat"]), float(j[0]["lon"])

            olat, olon = await geocode(origin)
            await asyncio.sleep(1.1)  # Nominatim usage policy
            dlat, dlon = await geocode(destination)
            r = await client.get(
                f"https://router.project-osrm.org/route/v1/driving/{olon},{olat};{dlon},{dlat}",
                params={"overview": "full", "geometries": "polyline", "alternatives": "true"},
            )
            r.raise_for_status()
            routes = r.json().get("routes") or []
            if not routes:
                raise Exception("OSRM returned no routes")
            key = (lambda x: x["distance"]) if routing_mode == "shortest" else (lambda x: x["duration"])
            best = min(routes, key=key)
            return {
                "distance_meters": int(round(best["distance"])),
                "duration_seconds": int(round(best["duration"])),
                "duration_in_traffic_seconds": int(round(best["duration"])),
                "static_duration_seconds": int(round(best["duration"])),
                "polyline": best["geometry"],
                "traffic_aware": False,
                "routing_mode": routing_mode,
                "source": "osrm_fallback",
            }

    async def get_route(
        self,
        origin: str,
        destination: str,
        routing_mode: str = "fastest",
        origin_place_id: str | None = None,
        destination_place_id: str | None = None,
    ):
        kwargs = dict(
            routing_mode=routing_mode,
            origin_place_id=origin_place_id,
            destination_place_id=destination_place_id,
        )
        cache_path = self._route_cache_path(
            origin, destination, routing_mode, origin_place_id, destination_place_id
        )
        try:
            route = await self._google_route(origin, destination, **kwargs)
            if route.get("source") == "google_routes_api" and self.transport is None:
                try:
                    os.makedirs(self.ROUTE_CACHE_DIR, exist_ok=True)
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(route, f)
                except Exception as e:
                    print(f"[ROUTES] cache write failed: {e}")
            return route
        except Exception as google_err:
            print(f"[ROUTES] Google failed: {google_err}")
            if self.transport is not None or os.getenv("ACTA_DISABLE_ROUTE_FALLBACK"):
                raise
            # 1) last good Google route for this exact pair
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                cached["source"] = "google_routes_api_cached"
                print("[ROUTES] Using cached Google route")
                return cached
            except Exception:
                pass
            # 2) free OSRM + Nominatim
            try:
                route = await self._osrm_route(origin, destination, routing_mode)
                print("[ROUTES] Using OSRM fallback route")
                return route
            except Exception as osrm_err:
                raise Exception(f"{google_err} | OSRM fallback also failed: {osrm_err}")
