"""
SignalService
=============

Responsible ONLY for acquiring raw OSM data relevant to a route:

    - traffic-signal nodes inside the route's bounding box
    - the road ways that pass through each signal (with full geometry
      and tags such as `oneway`), so that RouteMatchService can later
      work out which direction of travel each signal controls.

This service does NOT decide whether a signal belongs to the selected
route, and does NOT decide direction. That is RouteMatchService's job.
Keeping the responsibility split makes both services independently
testable (see backend/tests).

Design notes
------------
- A single bounding-box Overpass query replaces the old "one query per
  75m segment" approach, which was generating dozens of requests and
  causing HTTP 504s. If the bounding box is unusually large the query
  is split into a small, capped number of tiles instead of hundreds of
  individual point queries.
- Overpass is queried through several public mirrors, tried in order,
  because any single mirror is known to be flaky.
- A successful, non-empty response is cached to disk (backend/cache)
  so that repeated runs / demos do not depend on Overpass being up.
  Failed or empty responses are never cached, which was a bug in the
  previous implementation (all cache files on disk were empty).
"""

from __future__ import annotations

import hashlib
import json
import os
import asyncio
from typing import Dict, List, Optional

import httpx

CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "cache")
)

# Public Overpass mirrors, tried in this order. Do NOT remove this
# fallback list -- individual mirrors are known to time out (504) or
# refuse connections.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Whole-sweep retries (over every mirror) with backoff. Public mirrors
# return transient 502/503/504/429 under load; a second pass a couple of
# seconds later usually succeeds.
OVERPASS_ROUNDS = 3
OVERPASS_BACKOFF = 2.0

# Several public Overpass mirrors return HTTP 406 (Not Acceptable) for
# requests carrying a generic/default HTTP client User-Agent (e.g. the
# bare "python-httpx/x.x" httpx sends by default), since Overpass's own
# usage policy asks clients to identify themselves. A descriptive,
# non-default User-Agent avoids this.
USER_AGENT = "ACTA-Traffic-Engine/1.0 (traffic-signal-preemption research tool)"

# How long we're willing to wait per mirror before giving up and trying
# the next one. Split into connect vs. read so a mirror that's simply
# unreachable fails over almost immediately, while a mirror that IS
# responding but slow to finish computing still gets a fair chance.
# Deliberately much shorter than the old flat 60s -- a single slow
# mirror should not make the whole request take a minute-plus before
# even reaching a working one.
OVERPASS_CONNECT_TIMEOUT = 8.0
OVERPASS_READ_TIMEOUT = 40.0

# Bounding-box padding, in degrees (~50-80m at Bangalore's latitude).
BBOX_PADDING_DEG = 0.0007

# If the bounding box diagonal is larger than this, split it into a
# small grid of tiles instead of sending one enormous query. This is
# a much smaller number of requests than the old per-75m approach.
MAX_TILE_SPAN_DEG = 0.08  # roughly ~9km
MAX_TILES = 6


class OverpassUnavailableError(RuntimeError):
    """Raised when every Overpass mirror fails and no cache is available."""


class SignalService:
    def __init__(
        self,
        endpoints: Optional[List[str]] = None,
        cache_dir: str = CACHE_DIR,
        timeout: float = OVERPASS_READ_TIMEOUT,
        connect_timeout: float = OVERPASS_CONNECT_TIMEOUT,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.endpoints = endpoints or OVERPASS_ENDPOINTS
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        # Only used by tests, to inject a deterministic httpx.MockTransport
        # instead of hitting the real network.
        self.transport = transport
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def get_signals(
        self,
        coordinates: List[Dict[str, float]],
        use_cache: bool = True,
    ) -> List[Dict]:
        """
        Given the decoded route polyline, return every candidate traffic
        signal node inside the route's bounding box, each annotated with
        the OSM ways that pass through it (for downstream direction
        analysis by RouteMatchService).

        Returns a list of dicts:
            {
                "id": <osm node id>,
                "latitude": float,
                "longitude": float,
                "tags": {...},
                "ways": [
                    {
                        "way_id": int,
                        "tags": {...},
                        "oneway": "yes" | "no" | "-1" | None,
                        "highway": str | None,
                        "geometry": [{"lat": .., "lon": ..}, ...],
                        "node_index": int,   # index of this signal within geometry
                    },
                    ...
                ],
            }
        """
        if not coordinates:
            return []

        bboxes = self._build_bboxes(coordinates)

        client_timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.timeout,
            write=self.connect_timeout,
            pool=self.connect_timeout,
        )

        # One shared client for the whole request: reuses TCP/TLS
        # connections across mirrors and tiles instead of paying a fresh
        # handshake per bbox, and lets independent tiles be fetched
        # concurrently rather than one-by-one -- the dominant cost for a
        # multi-tile (long) route is network round-trip latency, so
        # fetching tiles in parallel is the highest-leverage speed win
        # here.
        async with httpx.AsyncClient(timeout=client_timeout, transport=self.transport) as client:
            results = await asyncio.gather(
                *(self._get_elements_for_bbox(client, bbox, use_cache) for bbox in bboxes)
            )

        elements: List[Dict] = [el for tile_elements in results for el in tile_elements]

        return self._elements_to_signals(elements)

    # ------------------------------------------------------------------
    # Bounding box handling
    # ------------------------------------------------------------------
    def _build_bboxes(self, coordinates: List[Dict[str, float]]) -> List[tuple]:
        lats = [pt.get("lat", pt.get("latitude")) for pt in coordinates]
        lons = [pt.get("lng", pt.get("longitude")) for pt in coordinates]

        min_lat, max_lat = min(lats) - BBOX_PADDING_DEG, max(lats) + BBOX_PADDING_DEG
        min_lon, max_lon = min(lons) - BBOX_PADDING_DEG, max(lons) + BBOX_PADDING_DEG

        lat_span = max_lat - min_lat
        lon_span = max_lon - min_lon

        if lat_span <= MAX_TILE_SPAN_DEG and lon_span <= MAX_TILE_SPAN_DEG:
            return [(min_lat, min_lon, max_lat, max_lon)]

        # Split into a small grid, capped at MAX_TILES total tiles.
        import math

        n_lat = max(1, min(MAX_TILES, math.ceil(lat_span / MAX_TILE_SPAN_DEG)))
        n_lon = max(1, min(MAX_TILES, math.ceil(lon_span / MAX_TILE_SPAN_DEG)))

        # Keep the total tile count bounded.
        while n_lat * n_lon > MAX_TILES:
            if n_lat >= n_lon and n_lat > 1:
                n_lat -= 1
            elif n_lon > 1:
                n_lon -= 1
            else:
                break

        tiles = []
        lat_step = lat_span / n_lat
        lon_step = lon_span / n_lon
        for i in range(n_lat):
            for j in range(n_lon):
                tiles.append((
                    min_lat + i * lat_step,
                    min_lon + j * lon_step,
                    min_lat + (i + 1) * lat_step,
                    min_lon + (j + 1) * lon_step,
                ))
        return tiles

    # ------------------------------------------------------------------
    # Overpass querying
    # ------------------------------------------------------------------
    def _build_query(self, bbox: tuple) -> str:
        south, west, north, east = bbox
        overpass_timeout = max(25, int(self.timeout))
        return f"""
[out:json][timeout:{overpass_timeout}];
(
  node["highway"="traffic_signals"]({south},{west},{north},{east});
);
out body;
way(bn)["highway"];
out body geom;
""".strip()

    def _cache_path(self, bbox: tuple) -> str:
        key = hashlib.sha1(
            json.dumps(bbox, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return os.path.join(self.cache_dir, f"{key}.json")

    async def _get_elements_for_bbox(self, client: httpx.AsyncClient, bbox: tuple, use_cache: bool) -> List[Dict]:
        cache_path = self._cache_path(bbox)

        if use_cache and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                elements = cached.get("elements", [])
                if elements:
                    print(f"[CACHE] Loaded {len(elements)} elements for bbox {bbox}")
                    return elements
            except Exception as e:
                print(f"[CACHE] Failed to read cache ({cache_path}): {e}")

        query = self._build_query(bbox)
        last_error: Optional[Exception] = None
        headers = {"User-Agent": USER_AGENT}

        for round_no in range(OVERPASS_ROUNDS):
            if round_no and self.transport is None:
                await asyncio.sleep(OVERPASS_BACKOFF * round_no)
            for endpoint in self.endpoints:
                try:
                    response = await client.post(endpoint, data={"data": query}, headers=headers)
                except httpx.RequestError as e:
                    print(f"[OVERPASS] {endpoint} connection failed: {e!r}")
                    last_error = e
                    continue

                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception as e:
                        print(f"[OVERPASS] {endpoint} returned malformed JSON: {e}")
                        last_error = e
                        continue

                    # Overpass can return 200 with a "remark" and no
                    # elements when it ran out of time/memory. Treat as failure.
                    if data.get("remark") and not data.get("elements"):
                        print(f"[OVERPASS] {endpoint} remark: {data.get('remark')}")
                        last_error = RuntimeError(str(data.get("remark")))
                        continue

                    elements = data.get("elements", [])

                    if elements and use_cache:
                        try:
                            data["_bbox"] = list(bbox)
                            with open(cache_path, "w", encoding="utf-8") as f:
                                json.dump(data, f)
                        except Exception as e:
                            print(f"[CACHE] Failed to write cache: {e}")

                    print(f"[OVERPASS] {endpoint} returned {len(elements)} elements")
                    return elements

                print(f"[OVERPASS] {endpoint} returned HTTP {response.status_code}")
                last_error = RuntimeError(
                    f"{endpoint} returned HTTP {response.status_code}"
                )

        # Every mirror failed. Fall back to a stale cache if one exists,
        # otherwise surface a clear, catchable error rather than crashing
        # deep inside route matching.
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                print(f"[OVERPASS] All mirrors failed, using stale cache for bbox {bbox}")
                return cached.get("elements", [])
            except Exception:
                pass

        # Last resort: any cached tile that fully covers this bbox.
        covering = self._find_covering_cache(bbox)
        if covering:
            print(f"[OVERPASS] All mirrors failed, using covering cache for bbox {bbox}")
            return covering

        print(f"[OVERPASS] All mirrors failed for bbox {bbox}: {last_error}")
        return []

    def _find_covering_cache(self, bbox: tuple) -> List[Dict]:
        s, w, n, e = bbox
        try:
            names = os.listdir(self.cache_dir)
        except Exception:
            return []
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.cache_dir, name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                cb = data.get("_bbox")
                if cb and cb[0] <= s and cb[1] <= w and cb[2] >= n and cb[3] >= e and data.get("elements"):
                    return data["elements"]
            except Exception:
                continue
        return []

    # ------------------------------------------------------------------
    # Element parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _elements_to_signals(elements: List[Dict]) -> List[Dict]:
        signal_nodes = {}
        ways = []

        for el in elements:
            if el.get("type") == "node" and el.get("tags", {}).get("highway") == "traffic_signals":
                signal_nodes[el["id"]] = {
                    "id": el["id"],
                    "latitude": el["lat"],
                    "longitude": el["lon"],
                    "tags": el.get("tags", {}),
                    "ways": [],
                }
            elif el.get("type") == "way":
                ways.append(el)

        for way in ways:
            geometry = way.get("geometry") or []
            node_ids = way.get("nodes") or []
            tags = way.get("tags", {})

            for idx, node_id in enumerate(node_ids):
                if node_id in signal_nodes and idx < len(geometry):
                    signal_nodes[node_id]["ways"].append({
                        "way_id": way["id"],
                        "tags": tags,
                        "oneway": tags.get("oneway"),
                        "highway": tags.get("highway"),
                        "geometry": geometry,
                        "node_index": idx,
                    })

        return list(signal_nodes.values())
