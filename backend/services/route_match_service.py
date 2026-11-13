"""
RouteMatchService
==================

Responsible for all route/signal *geometric and directional* matching:

    - building directed route geometry from the decoded Google polyline
    - projecting a signal onto that geometry (distance + position along route)
    - deciding ON_ROUTE / OFF_ROUTE
    - deciding A_TO_B / B_TO_A / UNKNOWN using OSM way geometry + `oneway`
      tags, not just raw proximity
    - deduplicating genuinely-duplicate OSM signal nodes
    - producing the final, ordered list of route signals

Why proximity alone is not enough
----------------------------------
Every point in space has a nearest point on any route -- that does not
mean it belongs to the route. Two OSM traffic-signal nodes can sit a few
metres apart at the same junction while controlling opposite carriageways
(A->B vs B->A). So membership is decided from THREE signals together:

    1. distance_from_route_meters   -- is it even near the corridor?
    2. bearing agreement            -- does the road it sits on run the
                                        same way as the route at that point?
    3. `oneway` on the associated OSM way -- if the way is one-directional,
                                        which way does that direction point?

A signal is only kept (ON_ROUTE / A_TO_B) when the geometry is close AND
the direction evidence points the same way as travel from A to B.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

# Default matching thresholds. These are geometric matching parameters,
# not ACTA trigger thresholds (those live in road_profiles.json and are
# used by ActaEngine instead).
DEFAULT_CORRIDOR_THRESHOLD_M = 35.0
DEFAULT_ANGLE_TOLERANCE_DEG = 45.0
DUPLICATE_COORD_TOLERANCE_DEG = 1e-6  # ~11cm - only exact/near-exact duplicates


class RouteMatchService:

    def __init__(
        self,
        corridor_threshold_m: float = DEFAULT_CORRIDOR_THRESHOLD_M,
        angle_tolerance_deg: float = DEFAULT_ANGLE_TOLERANCE_DEG,
    ):
        self.corridor_threshold_m = corridor_threshold_m
        self.angle_tolerance_deg = angle_tolerance_deg

    # -------------------------------------------------
    # Haversine distance
    # -------------------------------------------------
    def haversine(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        R = 6371000  # meters

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)

        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )

        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Initial compass bearing (degrees, 0-360) from point 1 to point 2."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_lambda = math.radians(lon2 - lon1)

        x = math.sin(d_lambda) * math.cos(phi2)
        y = (
            math.cos(phi1) * math.sin(phi2)
            - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
        )

        theta = math.atan2(x, y)
        return (math.degrees(theta) + 360) % 360

    @staticmethod
    def angle_diff(a: float, b: float) -> float:
        """Smallest absolute difference between two compass bearings, 0-180."""
        d = abs(a - b) % 360
        return min(d, 360 - d)

    # -------------------------------------------------
    # Create route geometry
    # -------------------------------------------------
    def create_route_geometry(self, coordinates: list) -> dict:
        total_distance = 0.0
        segments = []

        for i in range(len(coordinates) - 1):
            p1 = coordinates[i]
            p2 = coordinates[i + 1]

            distance = self.haversine(
                p1["lat"],
                p1["lng"],
                p2["lat"],
                p2["lng"]
            )

            segments.append({
                "start": p1,
                "end": p2,
                "distance": distance,
                "start_distance": total_distance,
                "end_distance": total_distance + distance,
                "bearing": self.bearing(p1["lat"], p1["lng"], p2["lat"], p2["lng"]),
            })

            total_distance += distance

        return {
            "coordinates": coordinates,
            "segments": segments,
            "length": total_distance
        }

    # -------------------------------------------------
    # Project signal onto route
    # -------------------------------------------------
    def signal_position_on_route(
        self,
        signal: dict,
        route_geometry: dict
    ) -> dict:
        signal_lat = signal["latitude"]
        signal_lng = signal["longitude"]

        best_distance = float("inf")
        best_position = None
        best_route_point = None
        best_segment_bearing = None

        for segment in route_geometry["segments"]:
            start = segment["start"]
            end = segment["end"]

            lat1 = math.radians(start["lat"])
            lon1 = math.radians(start["lng"])

            lat2 = math.radians(end["lat"])
            lon2 = math.radians(end["lng"])

            lat = math.radians(signal_lat)
            lon = math.radians(signal_lng)

            # Local projection
            x1 = lon1 * math.cos(lat1)
            y1 = lat1

            x2 = lon2 * math.cos(lat1)
            y2 = lat2

            x = lon * math.cos(lat1)
            y = lat

            dx = x2 - x1
            dy = y2 - y1

            segment_length_squared = dx * dx + dy * dy

            if segment_length_squared == 0:
                t = 0.0
            else:
                t = ((x - x1) * dx + (y - y1) * dy) / segment_length_squared
                t = max(0.0, min(1.0, t))

            projected_x = x1 + t * dx
            projected_y = y1 + t * dy

            projected_lat = math.degrees(projected_y)
            projected_lon = math.degrees(projected_x / math.cos(lat1))

            distance = self.haversine(
                signal_lat,
                signal_lng,
                projected_lat,
                projected_lon
            )

            if distance < best_distance:
                best_distance = distance
                segment_distance = segment["distance"] * t
                best_position = segment["start_distance"] + segment_distance
                best_route_point = {
                    "latitude": projected_lat,
                    "longitude": projected_lon
                }
                best_segment_bearing = segment["bearing"]

        return {
            "distance_from_route_meters": best_distance,
            "position_on_route_meters": best_position,
            "nearest_route_point": best_route_point,
            "local_route_bearing": best_segment_bearing,
        }

    # -------------------------------------------------
    # Direction determination from OSM way geometry
    # -------------------------------------------------
    def _way_travel_bearing(self, way: dict) -> Optional[float]:
        """
        Bearing of the road AT the signal node, using the way's own
        digitized geometry (not the route). Returns None if there isn't
        enough geometry around the node to compute a bearing.
        """
        geometry = way.get("geometry") or []
        idx = way.get("node_index")

        if idx is None or not geometry or len(geometry) < 2:
            return None

        prev_pt = geometry[idx - 1] if idx > 0 else None
        next_pt = geometry[idx + 1] if idx < len(geometry) - 1 else None

        if prev_pt and next_pt:
            return self.bearing(prev_pt["lat"], prev_pt["lon"], next_pt["lat"], next_pt["lon"])
        if next_pt:
            return self.bearing(geometry[idx]["lat"], geometry[idx]["lon"], next_pt["lat"], next_pt["lon"])
        if prev_pt:
            return self.bearing(prev_pt["lat"], prev_pt["lon"], geometry[idx]["lat"], geometry[idx]["lon"])
        return None

    def _classify_way_against_route(self, way: dict, route_bearing: float) -> Optional[str]:
        """
        Returns "A_TO_B", "B_TO_A", "AMBIGUOUS" or None (no usable
        geometry) for a single OSM way relative to the route's local
        heading.
        """
        travel_bearing = self._way_travel_bearing(way)
        if travel_bearing is None:
            return None

        oneway = (way.get("oneway") or "").lower()

        if oneway == "-1":
            travel_bearing = (travel_bearing + 180) % 360
            diff = self.angle_diff(travel_bearing, route_bearing)
            return "A_TO_B" if diff <= self.angle_tolerance_deg else "B_TO_A"

        if oneway == "yes" or oneway == "true" or oneway == "1":
            diff = self.angle_diff(travel_bearing, route_bearing)
            return "A_TO_B" if diff <= self.angle_tolerance_deg else "B_TO_A"

        # Two-way (or untagged) road: the way itself doesn't restrict
        # direction, but its alignment (mod 180) tells us whether it is
        # even the same corridor as the route at this point.
        diff_forward = self.angle_diff(travel_bearing, route_bearing)
        diff_reverse = self.angle_diff((travel_bearing + 180) % 360, route_bearing)
        best = min(diff_forward, diff_reverse)

        if best <= self.angle_tolerance_deg:
            return "A_TO_B"  # aligned corridor, direction not restricted by OSM
        return "AMBIGUOUS"

    def classify_signal(self, signal: dict, route_geometry: dict) -> dict:
        """
        Full ON_ROUTE/OFF_ROUTE + direction classification for one signal.

        Returns a structured result, e.g.:
            {
                "signal_id": 123,
                "on_route": True,
                "direction": "A_TO_B",
                "position_on_route_meters": 5234.2,
                "distance_from_route_meters": 4.1,
                "match_confidence": 0.94,
            }
        or, for a discarded signal:
            {
                "signal_id": 456,
                "on_route": False,
                "direction": "B_TO_A",
                "reason": "opposite_direction",
            }
        """
        projection = self.signal_position_on_route(signal, route_geometry)
        distance = projection["distance_from_route_meters"]
        route_bearing = projection["local_route_bearing"]

        base = {
            "signal_id": signal["id"],
            "latitude": signal["latitude"],
            "longitude": signal["longitude"],
            "distance_from_route_meters": distance,
            "position_on_route_meters": projection["position_on_route_meters"],
        }

        if distance > self.corridor_threshold_m:
            return {
                **base,
                "on_route": False,
                "direction": "UNKNOWN",
                "reason": "off_route",
            }

        ways = signal.get("ways") or []
        votes = [self._classify_way_against_route(w, route_bearing) for w in ways]
        votes = [v for v in votes if v is not None]

        if not votes:
            # No usable OSM way geometry -- we cannot defend a direction
            # call. Do not guess; report it honestly.
            return {
                **base,
                "on_route": False,
                "direction": "UNKNOWN",
                "reason": "direction_unknown",
                "match_confidence": 0.3,
            }

        if "A_TO_B" in votes:
            confidence = self._confidence(distance)
            return {
                **base,
                "on_route": True,
                "direction": "A_TO_B",
                "match_confidence": confidence,
            }

        if "B_TO_A" in votes and "AMBIGUOUS" not in votes:
            return {
                **base,
                "on_route": False,
                "direction": "B_TO_A",
                "reason": "opposite_direction",
            }

        return {
            **base,
            "on_route": False,
            "direction": "UNKNOWN",
            "reason": "ambiguous_direction",
            "match_confidence": 0.2,
        }

    def _confidence(self, distance_m: float) -> float:
        """Confidence for a signal already classified as ON_ROUTE/A_TO_B:
        closer to the route corridor -> higher confidence."""
        distance_score = max(0.0, 1.0 - (distance_m / self.corridor_threshold_m))
        confidence = 0.6 + 0.4 * distance_score
        return round(min(1.0, max(0.0, confidence)), 2)

    # -------------------------------------------------
    # Deduplication
    # -------------------------------------------------
    def deduplicate_signals(self, signals: List[Dict]) -> List[Dict]:
        """
        Remove genuinely duplicate OSM nodes (identical coordinates --
        i.e. the same physical entity mapped twice), WITHOUT merging
        distinct signal heads/movements that merely sit close together
        at the same junction. Distance-based merging is deliberately
        not used here; only exact-coordinate duplicates are collapsed.
        """
        seen = {}
        result = []

        for signal in signals:
            key = (
                round(signal["latitude"] / DUPLICATE_COORD_TOLERANCE_DEG),
                round(signal["longitude"] / DUPLICATE_COORD_TOLERANCE_DEG),
            )
            if key in seen:
                continue
            seen[key] = True
            result.append(signal)

        return result

    # -------------------------------------------------
    # Full pipeline: classify + order
    # -------------------------------------------------
    def match_route_signals(self, signals: List[Dict], route_geometry: dict) -> Dict:
        """
        Runs deduplication + classification over every candidate signal
        and returns both the ordered on-route signals and the full
        classification report (kept + discarded), for logging/debugging.
        """
        deduped = self.deduplicate_signals(signals)

        classifications = [self.classify_signal(s, route_geometry) for s in deduped]

        on_route = [c for c in classifications if c.get("on_route")]
        on_route.sort(key=lambda c: c["position_on_route_meters"])

        discarded = [c for c in classifications if not c.get("on_route")]

        return {
            "on_route_signals": on_route,
            "discarded_signals": discarded,
            "all_signals": classifications,
        }

    # Backwards-compatible alias used by earlier scripts/tests.
    def is_signal_on_route(self, signal: dict, route_geometry: dict) -> dict:
        return self.signal_position_on_route(signal, route_geometry)
