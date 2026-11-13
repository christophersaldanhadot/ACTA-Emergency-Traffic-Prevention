"""
route_builder.py
=================

Builds the .rou.xml for the single-corridor scenario:

  - one ambulance ("emergency" vClass), routed A -> B along the full
    corridor, departing shortly after simulation start
  - one "ego_car" (strictly vClass="passenger"/guiShape="passenger" --
    never emergency, never any other class), routed A -> B along the
    same corridor via duarouter's passenger-only routing (see
    duarouter_ego.py). Its speedFactor is calibrated from live Google
    Maps traffic data (see traffic_calibration.py) so a no-preemption
    run of ego_car reproduces Google's current live-traffic ETA for
    this corridor, instead of always driving at a fixed free-flow
    speed regardless of real-world conditions.
  - light background traffic on the main road (both directions) and
    on each junction's minor cross street, so the traffic lights have
    real conflicting demand and preemption has a visible effect
    (otherwise there would be nothing for a signal to hold back)
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

AMBULANCE_SPEED_MS = 19.4  # ~70 km/h
AMBULANCE_DEPART_TIME = 5
EGO_CAR_DEPART_TIME = 2
DEFAULT_END_TIME = 1200


def build_routes(
    network_meta: Dict,
    out_dir: str,
    background_traffic: bool = True,
    end_time: int = DEFAULT_END_TIME,
    ego_route_edges: Optional[List[str]] = None,
    ego_speed_factor: float = 1.0,
) -> str:
    """
    ego_route_edges: the passenger-only edge sequence for ego_car, as
        computed by duarouter_ego.compute_ego_route_edges(). Omitted
        (None) only for callers that don't need the calibrated ego_car
        (e.g. isolated unit tests) -- normal build_scenario() callers
        always pass it.
    ego_speed_factor: from traffic_calibration.compute_ego_speed_factor().
    """
    os.makedirs(out_dir, exist_ok=True)
    rou_path = os.path.join(out_dir, "scenario.rou.xml")

    main_fwd = " ".join(network_meta["main_fwd_edges"])
    main_rev = " ".join(reversed(network_meta["main_rev_edges"]))

    lines = ["<routes>\n"]

    lines.append(
        '    <vType id="ambulance" vClass="emergency" length="6.5" '
        f'maxSpeed="{AMBULANCE_SPEED_MS:.1f}" guiShape="emergency" '
        'color="1,0,0" speedFactor="1.0" sigma="0"/>\n'
    )
    lines.append(
        '    <vType id="car" vClass="passenger" length="4.5" '
        'maxSpeed="13.9" guiShape="passenger" sigma="0.5"/>\n'
    )
    # Hardcoded strictly to vClass="passenger" -- this is the vehicle
    # whose travel time is calibrated against live Google traffic data,
    # so it must never be able to drift onto an emergency-only path or
    # inherit any non-passenger behaviour.
    lines.append(
        '    <vType id="ego_car" vClass="passenger" guiShape="passenger" '
        'length="4.5" sigma="0" '
        f'speedFactor="{ego_speed_factor:.4f}"/>\n'
    )

    lines.append(f'    <route id="ambulance_route" edges="{main_fwd}"/>\n')

    if ego_route_edges:
        lines.append(f'    <route id="ego_route" edges="{" ".join(ego_route_edges)}"/>\n')

    if background_traffic:
        lines.append(f'    <route id="bg_main_fwd" edges="{main_fwd}"/>\n')
        lines.append(f'    <route id="bg_main_rev" edges="{main_rev}"/>\n')
        lines.append(
            f'    <flow id="flow_main_fwd" type="car" route="bg_main_fwd" '
            f'begin="0" end="{end_time}" period="9" departSpeed="max"/>\n'
        )
        lines.append(
            f'    <flow id="flow_main_rev" type="car" route="bg_main_rev" '
            f'begin="0" end="{end_time}" period="11" departSpeed="max"/>\n'
        )

        for signal_id, node_id in network_meta["signal_node"].items():
            in_s = f"{node_id}_cross_in_s"
            out_n = f"{node_id}_cross_out_n"
            in_n = f"{node_id}_cross_in_n"
            out_s = f"{node_id}_cross_out_s"
            route_a = f"cross_{node_id}_s2n"
            route_b = f"cross_{node_id}_n2s"
            lines.append(f'    <route id="{route_a}" edges="{in_s} {out_n}"/>\n')
            lines.append(f'    <route id="{route_b}" edges="{in_n} {out_s}"/>\n')
            lines.append(
                f'    <flow id="flow_{route_a}" type="car" route="{route_a}" '
                f'begin="0" end="{end_time}" period="20" departSpeed="max"/>\n'
            )
            lines.append(
                f'    <flow id="flow_{route_b}" type="car" route="{route_b}" '
                f'begin="0" end="{end_time}" period="23" departSpeed="max"/>\n'
            )

    # ego_car and the ambulance are declared LAST, in non-decreasing
    # depart order (SUMO requires route-file vehicle entries sorted by
    # departure time): every background flow above begins at t=0,
    # ego_car departs at EGO_CAR_DEPART_TIME, then the ambulance at
    # AMBULANCE_DEPART_TIME (> EGO_CAR_DEPART_TIME).
    if ego_route_edges:
        lines.append(
            f'    <vehicle id="ego_car_0" type="ego_car" route="ego_route" '
            f'depart="{EGO_CAR_DEPART_TIME}" departSpeed="max" departLane="best"/>\n'
        )

    lines.append(
        f'    <vehicle id="ambulance_0" type="ambulance" route="ambulance_route" '
        f'depart="{AMBULANCE_DEPART_TIME}" departSpeed="max" departLane="best"/>\n'
    )

    lines.append("</routes>\n")

    with open(rou_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    return rou_path
