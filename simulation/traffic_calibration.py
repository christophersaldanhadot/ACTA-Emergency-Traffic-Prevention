"""
traffic_calibration.py
=======================

Turns the two Google Routes API timings already carried on an ACTA
plan (`route_static_duration_seconds` = free-flow, no-traffic time;
`route_duration_in_traffic_seconds` = live, traffic-aware time) into a
SUMO `speedFactor` for the "ego_car" vType, so a no-preemption run of
ego_car down the corridor takes approximately as long in SUMO as
Google currently reports for a real passenger car on that road right
now.

    congestion_factor = duration_in_traffic / static_duration
        1.0  -> road is currently running at free-flow speed
        >1.0 -> currently slower than free-flow (congested); e.g. 1.6
                means the live trip is taking 60% longer than free-flow

    ego_speed_factor = 1 / congestion_factor
        SUMO's per-vehicle speedFactor scales the vehicle's desired
        speed relative to each edge's posted speed limit (which is
        itself the network's free-flow speed -- see
        network_builder.MAIN_LANE_SPEED_MS). Slowing ego_car down by
        the same ratio Google's live traffic is currently slower than
        Google's own free-flow estimate is what makes the two
        free-flow baselines (SUMO's and Google's) cancel out, leaving
        just the congestion ratio applied to the corridor.

This is a first-order calibration, not a guarantee of an exact-to-the-
second match: it accounts for the corridor's overall live-traffic slow
down, but not for the specific extra seconds SUMO's own signal-cycle
waiting adds on top (which Google's live ETA already bakes in from
real-world signal timing, but SUMO's synthetic cross-street signals
don't reproduce 1:1). It is what makes the no-preemption baseline
track real conditions instead of running at a fixed, unchanging
free-flow speed regardless of what Google is currently reporting.
"""

from __future__ import annotations

from typing import Optional

# Clamped so a missing/zero-traffic reading (speed_factor would
# otherwise be unbounded) or a very light live-traffic reading
# (ego_car faster than free-flow) can never produce a vehicle that
# effectively never arrives, or one that exceeds the road's own
# free-flow speed.
MIN_EGO_SPEED_FACTOR = 0.15
MAX_EGO_SPEED_FACTOR = 1.0
DEFAULT_EGO_SPEED_FACTOR = 1.0


def compute_congestion_factor(
    static_duration_s: Optional[float],
    duration_in_traffic_s: Optional[float],
) -> float:
    """duration_in_traffic / static_duration. Falls back to 1.0 (no
    congestion applied) if either Google figure is missing or
    non-positive -- e.g. an offline/mock plan, or a route for which
    Google didn't return a static_duration."""
    if not static_duration_s or not duration_in_traffic_s or static_duration_s <= 0:
        return 1.0
    return duration_in_traffic_s / static_duration_s


def compute_ego_speed_factor(
    static_duration_s: Optional[float],
    duration_in_traffic_s: Optional[float],
) -> float:
    """The ego_car speedFactor to apply for this plan's live-traffic
    reading, clamped to [MIN_EGO_SPEED_FACTOR, MAX_EGO_SPEED_FACTOR]."""
    congestion_factor = compute_congestion_factor(static_duration_s, duration_in_traffic_s)
    if congestion_factor <= 0:
        return DEFAULT_EGO_SPEED_FACTOR
    speed_factor = 1.0 / congestion_factor
    return max(MIN_EGO_SPEED_FACTOR, min(MAX_EGO_SPEED_FACTOR, speed_factor))
