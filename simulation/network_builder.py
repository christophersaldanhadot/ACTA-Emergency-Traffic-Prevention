"""
network_builder.py
===================

Builds a SUMO network for ONE road corridor from an ACTA plan
(the JSON shape returned by POST /generate-full-plan).

Scope, deliberately simplified (per project brief: "ignore different
lanes and exceptional cases, focus on one road"):

  - One through road, one lane per direction, modelled as a straight
    line whose node spacing preserves the REAL inter-signal distances
    from the matched route (not the real curved geometry -- ACTA's
    timing only depends on distance, not curvature).
  - Every ACTA on-route signal becomes a traffic-light junction on
    that road.
  - A short minor cross-street stub (dead-end, one lane each way) is
    added at every junction purely so netconvert generates a REAL
    multi-phase traffic light with something to hold red against --
    a junction with only one road has no conflicting movement and
    therefore no meaningful signal to preempt. The cross street is
    not a modelled real road; it exists only so preemption has a
    visible before/after effect.

Output: a compiled scenario.net.xml (via netconvert) plus a metadata
dict describing edge ids / tls ids for the route + preemption builder.
"""

from __future__ import annotations

import os
import subprocess
from typing import Dict, List

MAIN_LANE_SPEED_MS = 13.9      # ~50 km/h, main road
CROSS_LANE_SPEED_MS = 8.3      # ~30 km/h, minor cross street
CROSS_STUB_LENGTH_M = 30.0     # length of the synthetic minor approach
MIN_NODE_SPACING_M = 5.0       # minimum gap between consecutive main-line nodes

# vClass permissions. The main corridor carries both the calibrated
# "ego_car" (vClass="passenger") and the ambulance (vClass="emergency");
# the synthetic cross-street stubs only ever carry background car
# traffic, so they are passenger-only. Tagging edges this way (rather
# than leaving them unrestricted) is what lets duarouter's passenger-
# only ego_car routing (see duarouter_ego.py) mean something -- without
# an explicit `allow`, every vClass can already use every edge and the
# restriction would be a no-op.
ALLOW_MAIN = "passenger emergency"
ALLOW_CROSS = "passenger"


def _node_xml(node_id: str, x: float, y: float, node_type: str) -> str:
    return f'    <node id="{node_id}" x="{x:.2f}" y="{y:.2f}" type="{node_type}"/>\n'


def _edge_xml(edge_id: str, from_id: str, to_id: str, speed: float, lanes: int = 1, allow: str | None = None) -> str:
    allow_attr = f' allow="{allow}"' if allow else ""
    return (
        f'    <edge id="{edge_id}" from="{from_id}" to="{to_id}" '
        f'numLanes="{lanes}" speed="{speed:.2f}"{allow_attr}/>\n'
    )


def build_network(plan: Dict, out_dir: str) -> Dict:
    """
    plan: the dict returned by /generate-full-plan (must contain
          "route_distance_meters" and "signals" -- the ordered
          on-route signals, each with "signal_id" and
          "position_on_route_meters").

    Returns metadata:
        {
            "net_file": ".../scenario.net.xml",
            "main_nodes": ["n0", "n1", ..., "n_end"],
            "main_fwd_edges": ["main_fwd_0", "main_fwd_1", ...],  # A->B, in order
            "main_rev_edges": ["main_rev_0", ...],                # B->A, in order
            "signal_node": {signal_id: "n1", ...},
            "signal_tls": {signal_id: "n1", ...},  # tls id == junction id in SUMO
            "route_length_m": 15771.9,
        }
    """
    os.makedirs(out_dir, exist_ok=True)

    signals = plan["signals"]
    route_length = plan["route_distance_meters"]

    # Node positions along the corridor: start (0), each signal, end.
    raw_positions = [0.0] + [s["position_on_route_meters"] for s in signals] + [route_length]

    # Guard against zero-/near-zero-length edges (e.g. two OSM signals
    # that projected to the exact same point on the route, or a route
    # end that coincides with the last signal): enforce a small minimum
    # gap between consecutive nodes. This only nudges the SUMO geometry
    # -- it does not change the ACTA plan's own reported distances.
    positions = [raw_positions[0]]
    for pos in raw_positions[1:]:
        positions.append(max(pos, positions[-1] + MIN_NODE_SPACING_M))
    node_ids = ["n0"] + [f"n_sig_{s['signal_id']}" for s in signals] + ["n_end"]

    nodes_xml = ["<nodes>\n"]
    edges_xml = ["<edges>\n"]

    signal_node: Dict = {}
    signal_tls: Dict = {}

    for i, (node_id, pos) in enumerate(zip(node_ids, positions)):
        is_signal = 0 < i < len(node_ids) - 1
        node_type = "traffic_light" if is_signal else "priority"
        nodes_xml.append(_node_xml(node_id, pos, 0.0, node_type))

        if is_signal:
            signal_id = signals[i - 1]["signal_id"]
            signal_node[signal_id] = node_id
            signal_tls[signal_id] = node_id  # netconvert uses junction id as tls id

            # Minor cross-street stub (south + north dead ends) purely
            # to give this junction a real conflicting movement.
            south_id = f"{node_id}_s"
            north_id = f"{node_id}_n"
            nodes_xml.append(_node_xml(south_id, pos, -CROSS_STUB_LENGTH_M, "priority"))
            nodes_xml.append(_node_xml(north_id, pos, CROSS_STUB_LENGTH_M, "priority"))

            edges_xml.append(_edge_xml(f"{node_id}_cross_in_s", south_id, node_id, CROSS_LANE_SPEED_MS, allow=ALLOW_CROSS))
            edges_xml.append(_edge_xml(f"{node_id}_cross_out_s", node_id, south_id, CROSS_LANE_SPEED_MS, allow=ALLOW_CROSS))
            edges_xml.append(_edge_xml(f"{node_id}_cross_in_n", north_id, node_id, CROSS_LANE_SPEED_MS, allow=ALLOW_CROSS))
            edges_xml.append(_edge_xml(f"{node_id}_cross_out_n", node_id, north_id, CROSS_LANE_SPEED_MS, allow=ALLOW_CROSS))

    main_fwd_edges: List[str] = []
    main_rev_edges: List[str] = []
    for i in range(len(node_ids) - 1):
        fwd_id = f"main_fwd_{i}"
        rev_id = f"main_rev_{i}"
        edges_xml.append(_edge_xml(fwd_id, node_ids[i], node_ids[i + 1], MAIN_LANE_SPEED_MS, allow=ALLOW_MAIN))
        edges_xml.append(_edge_xml(rev_id, node_ids[i + 1], node_ids[i], MAIN_LANE_SPEED_MS, allow=ALLOW_MAIN))
        main_fwd_edges.append(fwd_id)
        main_rev_edges.append(rev_id)

    nodes_xml.append("</nodes>\n")
    edges_xml.append("</edges>\n")

    nod_path = os.path.join(out_dir, "scenario.nod.xml")
    edg_path = os.path.join(out_dir, "scenario.edg.xml")
    net_path = os.path.join(out_dir, "scenario.net.xml")

    with open(nod_path, "w", encoding="utf-8") as f:
        f.writelines(nodes_xml)
    with open(edg_path, "w", encoding="utf-8") as f:
        f.writelines(edges_xml)

    signal_tls_ids = list(signal_tls.values())
    # Desynchronize consecutive signals' default cycles (every other
    # junction offset by half a phase). Perfectly synchronized signals
    # would otherwise let a fast-moving vehicle ride a "green wave" by
    # coincidence, which would make it impossible to see any benefit
    # from ACTA preemption in a demo run.
    half_offset_ids = signal_tls_ids[1::2]

    netconvert_args = [
        "netconvert",
        "--node-files", nod_path,
        "--edge-files", edg_path,
        "--output-file", net_path,
        "--no-turnarounds", "true",
    ]
    if half_offset_ids:
        netconvert_args += ["--tls.half-offset", ",".join(half_offset_ids)]

    result = subprocess.run(netconvert_args, capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(net_path):
        raise RuntimeError(
            f"netconvert failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return {
        "net_file": net_path,
        "main_nodes": node_ids,
        "main_fwd_edges": main_fwd_edges,
        "main_rev_edges": main_rev_edges,
        "signal_node": signal_node,
        "signal_tls": signal_tls,
        "route_length_m": route_length,
        "netconvert_log": result.stderr,
    }
