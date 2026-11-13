"""
Deterministic fixtures for ACTA tests.

The synthetic route is a straight, due-east line at constant latitude
(12.9000). This makes bearings trivial to reason about: any way whose
digitized geometry also runs west->east has a ~90 degree bearing (same
as the route / "A_TO_B"); a way running east->west has a ~270 degree
bearing ("B_TO_A"); a way running north-south is roughly perpendicular
("AMBIGUOUS" / cross street).
"""

LAT = 12.9000


def build_route_coordinates():
    """25 points from lon 77.4900 to 77.5150 (~2.7km east-west route)."""
    start_lon = 77.4900
    end_lon = 77.5150
    n = 25
    step = (end_lon - start_lon) / (n - 1)
    return [{"lat": LAT, "lng": round(start_lon + step * i, 6)} for i in range(n)]


def _way(way_id, tags, geometry, node_index):
    return {
        "type": "way",
        "id": way_id,
        "tags": tags,
        "nodes": list(range(9_000_000 + way_id, 9_000_000 + way_id + len(geometry))),
        "geometry": geometry,
    }, node_index


def build_overpass_elements():
    """
    Returns a raw Overpass-style `elements` list containing:

      1001 - ON_ROUTE / A_TO_B    (eastbound oneway carriageway)
      1002 - OFF_ROUTE (B_TO_A)   (westbound oneway carriageway, same junction as 1001)
      1003 - OFF_ROUTE            (222m away from the route)
      1004 - OFF_ROUTE (ambiguous)(cross street, north-south, no oneway)
      1005 - OFF_ROUTE (unknown)  (no associated way data at all)
      1006 - ON_ROUTE / A_TO_B    (second eastbound signal, further east)
      1007 - exact duplicate of 1001 (same coordinates) -> collapsed by dedup
    """
    elements = []

    def node(node_id, lat, lon):
        return {"type": "node", "id": node_id, "lat": lat, "lon": lon,
                "tags": {"highway": "traffic_signals"}}

    # --- Signal 1001: eastbound, on-route, A_TO_B ---
    elements.append(node(1001, LAT, 77.5000))
    elements.append({
        "type": "way", "id": 501,
        "tags": {"highway": "primary", "oneway": "yes"},
        "nodes": [90010, 1001, 90011],
        "geometry": [
            {"lat": LAT, "lon": 77.4995},
            {"lat": LAT, "lon": 77.5000},
            {"lat": LAT, "lon": 77.5005},
        ],
    })

    # --- Signal 1002: westbound carriageway near the same junction, B_TO_A ---
    elements.append(node(1002, 12.90003, 77.50005))
    elements.append({
        "type": "way", "id": 502,
        "tags": {"highway": "primary", "oneway": "yes"},
        "nodes": [90020, 1002, 90021],
        "geometry": [
            {"lat": 12.90003, "lon": 77.50010},
            {"lat": 12.90003, "lon": 77.50005},
            {"lat": 12.90003, "lon": 77.50000},
        ],
    })

    # --- Signal 1003: far from the route entirely (~222m north) ---
    elements.append(node(1003, 12.9020, 77.5000))
    elements.append({
        "type": "way", "id": 503,
        "tags": {"highway": "residential", "oneway": "yes"},
        "nodes": [90030, 1003, 90031],
        "geometry": [
            {"lat": 12.9019, "lon": 77.5000},
            {"lat": 12.9020, "lon": 77.5000},
            {"lat": 12.9021, "lon": 77.5000},
        ],
    })

    # --- Signal 1004: cross street (north-south), sits right on the route line ---
    elements.append(node(1004, LAT, 77.5050))
    elements.append({
        "type": "way", "id": 504,
        "tags": {"highway": "secondary"},  # no oneway -> two-way
        "nodes": [90040, 1004, 90041],
        "geometry": [
            {"lat": 12.8995, "lon": 77.5050},
            {"lat": LAT, "lon": 77.5050},
            {"lat": 12.9005, "lon": 77.5050},
        ],
    })

    # --- Signal 1005: on the route line, but Overpass has no way data for it ---
    elements.append(node(1005, LAT, 77.5080))

    # --- Signal 1006: eastbound, on-route, A_TO_B, further along the route ---
    elements.append(node(1006, LAT, 77.5090))
    elements.append({
        "type": "way", "id": 506,
        "tags": {"highway": "primary", "oneway": "yes"},
        "nodes": [90060, 1006, 90061],
        "geometry": [
            {"lat": LAT, "lon": 77.5085},
            {"lat": LAT, "lon": 77.5090},
            {"lat": LAT, "lon": 77.5095},
        ],
    })

    # --- Signal 1007: exact duplicate OSM node of 1001 ---
    elements.append(node(1007, LAT, 77.5000))
    elements.append({
        "type": "way", "id": 507,
        "tags": {"highway": "primary", "oneway": "yes"},
        "nodes": [90070, 1007, 90071],
        "geometry": [
            {"lat": LAT, "lon": 77.4995},
            {"lat": LAT, "lon": 77.5000},
            {"lat": LAT, "lon": 77.5005},
        ],
    })

    return elements


GOOGLE_POLYLINE_SAMPLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
GOOGLE_POLYLINE_EXPECTED = [
    {"lat": 38.5, "lng": -120.2},
    {"lat": 40.7, "lng": -120.95},
    {"lat": 43.252, "lng": -126.453},
]
