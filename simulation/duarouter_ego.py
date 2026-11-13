"""
duarouter_ego.py
=================

Computes the "ego_car" vehicle's route through the compiled corridor
network with `duarouter`, instead of hand-listing the same
main_fwd_edges list that route_builder.py uses for the ambulance.

Why this matters here specifically: network_builder.py tags every edge
with an explicit `allow` (main road: "passenger,emergency"; synthetic
cross-street stubs: "passenger" only -- see ALLOW_MAIN / ALLOW_CROSS).
Handing duarouter a vClass="passenger" vehicle and asking it to route
from the corridor's first node to its last means the resulting path is
GENUINELY constrained by those permissions rather than assumed correct
-- if a future network ever introduced an edge that disallowed
"passenger", duarouter would route around it (or fail loudly), where a
hand-written edge list would silently send ego_car onto a road it
isn't allowed to use.
"""

from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, List

EGO_VTYPE_ID = "ego_car"

# Maps our routing_mode to the duarouter --weight-attribute value.
# Passed explicitly for BOTH modes (rather than relying on duarouter's
# default for "fastest") so the selected mode is always strictly
# enforced by an explicit flag, never an implicit default that could
# silently drift if duarouter's own default ever changed.
_WEIGHT_ATTRIBUTE_BY_MODE = {
    "fastest": "traveltime",
    "shortest": "length",
}


def compute_ego_route_edges(
    network_meta: Dict, out_dir: str, ego_speed_factor: float = 1.0, routing_mode: str = "fastest"
) -> List[str]:
    """Runs duarouter over the already-compiled scenario.net.xml for a
    single vClass="passenger" trip spanning the full corridor (first
    main_fwd edge -> last main_fwd edge), and returns the edge-id list
    duarouter actually chose.

    routing_mode:
      "fastest"  -> passes `--weight-attribute traveltime` explicitly,
                    so duarouter minimizes travel time (derived from
                    each edge's length/speed) as an explicit, strictly
                    enforced choice rather than an implicit default.
      "shortest" -> passes `--weight-attribute length` so duarouter
                    minimizes total edge length in meters instead of
                    travel time, i.e. a genuine shortest-distance path
                    rather than a fastest one.

    Raises RuntimeError if duarouter fails, finds no route, or (as a
    sanity check) somehow leaves the main-road corridor -- any of
    which means the passenger-only restriction isn't behaving as
    expected and should not be silently papered over.
    """
    if routing_mode not in ("fastest", "shortest"):
        raise ValueError(f"Unknown routing_mode '{routing_mode}' (expected 'fastest' or 'shortest').")

    os.makedirs(out_dir, exist_ok=True)

    net_file = network_meta["net_file"]
    main_fwd_edges = network_meta["main_fwd_edges"]
    from_edge = main_fwd_edges[0]
    to_edge = main_fwd_edges[-1]

    trips_path = os.path.join(out_dir, "ego_trip.trips.xml")
    duarouted_path = os.path.join(out_dir, "ego_route.rou.xml")

    trips_xml = (
        "<routes>\n"
        f'    <vType id="{EGO_VTYPE_ID}" vClass="passenger" guiShape="passenger" '
        f'length="4.5" sigma="0" speedFactor="{ego_speed_factor:.4f}"/>\n'
        f'    <trip id="{EGO_VTYPE_ID}" type="{EGO_VTYPE_ID}" depart="0" '
        f'from="{from_edge}" to="{to_edge}"/>\n'
        "</routes>\n"
    )
    with open(trips_path, "w", encoding="utf-8") as f:
        f.write(trips_xml)

    duarouter_args = [
        "duarouter",
        "--net-file", net_file,
        "--route-files", trips_path,
        "--output-file", duarouted_path,
        "--ignore-errors", "false",
        "--no-warnings", "true",
        # Explicit for both modes -- never rely on duarouter's implicit
        # default, so the selected mode is always strictly enforced.
        "--weight-attribute", _WEIGHT_ATTRIBUTE_BY_MODE[routing_mode],
    ]
    result = subprocess.run(duarouter_args, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(duarouted_path):
        raise RuntimeError(
            f"duarouter failed to compute a passenger-only ego_car route "
            f"(mode={routing_mode}, exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    tree = ET.parse(duarouted_path)
    root = tree.getroot()
    vehicle_el = root.find(f".//vehicle[@id='{EGO_VTYPE_ID}']")
    if vehicle_el is None:
        raise RuntimeError(
            "duarouter produced no route for ego_car -- no passenger-permitted "
            "path exists between the corridor endpoints."
        )
    route_el = vehicle_el.find("route")
    if route_el is None or not route_el.get("edges"):
        raise RuntimeError("duarouter's ego_car route output has no edges.")

    edges = route_el.get("edges").split()

    # Sanity check: on this single-corridor network the only legal
    # passenger path is straight down main_fwd_*; confirms duarouter's
    # restriction actually bit rather than silently wandering onto a
    # cross-street stub (which would indicate a modelling bug, not a
    # valid alternate route).
    main_edge_set = set(main_fwd_edges)
    if not all(e in main_edge_set for e in edges):
        raise RuntimeError(
            f"duarouter's passenger-only ego_car route left the main "
            f"corridor: {edges}"
        )

    total_distance_m = _sum_edge_lengths(net_file, edges)
    mode_label = "FastestTime" if routing_mode == "fastest" else "ShortestDistance"
    print(f"[INFO] Route computed (mode={mode_label}, {total_distance_m / 1000:.2f}km)")

    return edges


def _sum_edge_lengths(net_file: str, edge_ids: List[str]) -> float:
    """Sums the `length` attribute of each edge's lane(s) in the
    compiled .net.xml, using each edge's first lane as representative
    (SUMO net files store length per-lane; lanes on the same edge share
    the same length). Used only for the telemetry log line below --
    routing decisions themselves are made by duarouter, not this."""
    net_tree = ET.parse(net_file)
    net_root = net_tree.getroot()
    edge_id_set = set(edge_ids)
    lengths_by_edge: Dict[str, float] = {}
    for edge_el in net_root.findall("edge"):
        if edge_el.get("id") not in edge_id_set:
            continue
        lane_el = edge_el.find("lane")
        if lane_el is not None and lane_el.get("length") is not None:
            lengths_by_edge[edge_el.get("id")] = float(lane_el.get("length"))
    return sum(lengths_by_edge.get(e, 0.0) for e in edge_ids)
