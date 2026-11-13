"""
preemption_controller.py
=========================

Runs a SUMO simulation over TraCI and applies ACTA-driven signal
preemption to a single ambulance travelling the corridor.

Trigger mapping from ACTA cases (acta_plan segments) to SUMO actions:

    IMMEDIATE_TRIGGER
        The downstream (to_signal) is preempted the moment the
        ambulance reaches the upstream (from_signal) position.

    WAYPOINT_TRIGGER
        The downstream signal is preempted only once the ambulance
        reaches the segment's calculated waypoint position.

    MULTIPLE_WAYPOINTS
        Every waypoint except the last is an advisory / logged
        "pre-notification" event only (no signal action -- this models
        an early warning to a real controller so it can start clearing
        its queue). The LAST waypoint is the actual preemption trigger,
        same as a single WAYPOINT_TRIGGER.

The very first signal on the route has no upstream trigger rule from
the ACTA segment list (nothing precedes it), so it is preempted as
soon as the ambulance departs (dispatch-time preemption).

Preemption mechanics
---------------------
For each traffic-light junction, a dedicated SUMO TLS program
("preempt") is created dynamically via
`traci.trafficlight.setProgramLogic()`: a single static phase holding
the main-road approaches green and the minor cross-street red. On
trigger, the junction is switched to that program
(`setProgram(tls_id, "preempt")`). Once the ambulance has cleared the
junction by a safety margin, the junction is switched back to its
original default program ("0"), resuming normal operation.

Simplification: a real preemption controller would insert a brief
all-red clearance interval before the emergency-green phase if the
signal was displaying green for a conflicting approach. This demo
switches directly, which is a reasonable simplification for a single,
lightly-trafficked demo corridor (documented in the project README).
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import traci  # noqa: E402
from traci import trafficlight as tc_tls  # noqa: E402

CLEAR_DISTANCE_M = 25.0  # ambulance must be this far past a junction before it's released


def _build_trigger_events(plan: Dict) -> List[Dict]:
    """Flattens the ACTA plan into an ordered list of trigger events:
        {"position_m": float, "signal_ids": [..], "kind": "PREEMPT"|"ADVISORY", "case": str}
    """
    events: List[Dict] = []
    signals = plan["signals"]

    if signals:
        # Dispatch-time preemption for the very first on-route signal.
        events.append({
            "position_m": 0.0,
            "signal_ids": [signals[0]["signal_id"]],
            "kind": "PREEMPT",
            "case": "DISPATCH",
        })

    for segment in plan.get("acta_plan", []):
        case = segment["case"]

        if case == "IMMEDIATE_TRIGGER":
            from_pos = next(s["position_on_route_meters"] for s in signals if s["signal_id"] == segment["from_signal"])
            events.append({
                "position_m": from_pos,
                "signal_ids": [segment["to_signal"]],
                "kind": "PREEMPT",
                "case": case,
            })

        elif case == "WAYPOINT_TRIGGER":
            events.append({
                "position_m": segment["waypoint_meters"],
                "signal_ids": [segment["to_signal"]],
                "kind": "PREEMPT",
                "case": case,
            })

        elif case == "MULTIPLE_WAYPOINTS":
            waypoints = segment["waypoints_meters"]
            for wp in waypoints[:-1]:
                events.append({
                    "position_m": wp,
                    "signal_ids": [segment["to_signal"]],
                    "kind": "ADVISORY",
                    "case": case,
                })
            events.append({
                "position_m": waypoints[-1],
                "signal_ids": [segment["to_signal"]],
                "kind": "PREEMPT",
                "case": case,
            })

    events.sort(key=lambda e: e["position_m"])
    return events


def _main_green_state(tls_id: str) -> str:
    """Builds a state string holding ONLY the main-road THROUGH movement
    green (main-in -> main-out, both directions) and everything else --
    including turns from the main road onto the synthetic cross street,
    and the cross street itself -- red.

    Marking green for every connection whose INCOMING lane happens to be
    a main-road lane is not enough: a single main-road lane typically
    also has separate connections turning onto the cross street (e.g.
    "main_fwd -> cross_out_n" as a left turn). Those turning connections
    from opposite approaches can converge on the same outgoing lane,
    which SUMO correctly flags as an unsafe simultaneous-green conflict.
    The ambulance only ever travels straight through (see route_builder
    .py), so only the through connections need to be green.
    """
    def _is_main(lane_id: str) -> bool:
        return lane_id.startswith("main_fwd_") or lane_id.startswith("main_rev_")

    links = tc_tls.getControlledLinks(tls_id)
    state = []
    for link_group in links:
        is_through = any(
            _is_main(in_lane) and _is_main(out_lane)
            for (in_lane, out_lane, _via) in link_group
        ) if link_group else False
        state.append("G" if is_through else "r")
    return "".join(state)


class PreemptionController:
    def __init__(self, network_meta: Dict, plan: Dict, ambulance_id: str = "ambulance_0"):
        self.network_meta = network_meta
        self.plan = plan
        self.ambulance_id = ambulance_id
        self.events = _build_trigger_events(plan)
        self.log: List[Dict] = []
        self._active: Dict[str, float] = {}  # tls_id -> clear position (release once ambulance passes this)
        self._original_program: Dict[str, str] = {}
        self._preempt_program_ready = set()

        # Real physical position of each junction along the corridor,
        # used to decide WHEN TO RELEASE it. This is deliberately kept
        # separate from an event's "position_m" (WHEN TO FIRE), because
        # those two are not the same point: e.g. an IMMEDIATE_TRIGGER
        # fires when the ambulance reaches the UPSTREAM signal, but the
        # junction being preempted is the DOWNSTREAM one, further down
        # the route. Releasing based on the trigger's fire position
        # would clear the preemption long before the ambulance actually
        # arrives there.
        self._signal_position: Dict = {
            s["signal_id"]: s["position_on_route_meters"] for s in plan.get("signals", [])
        }

    def _ensure_preempt_program(self, tls_id: str):
        if tls_id in self._preempt_program_ready:
            return
        state = _main_green_state(tls_id)
        logic = tc_tls.Logic(
            "preempt", 0, 0,
            phases=[tc_tls.Phase(9999, state)],
        )
        tc_tls.setProgramLogic(tls_id, logic)
        self._preempt_program_ready.add(tls_id)

    def _preempt(self, tls_id: str, signal_id, sim_time: float, trigger_position: float, case: str):
        if tls_id not in self._original_program:
            self._original_program[tls_id] = tc_tls.getProgram(tls_id)
        self._ensure_preempt_program(tls_id)
        tc_tls.setProgram(tls_id, "preempt")

        # Release once the ambulance has passed THIS junction's real
        # position (not the position that fired the trigger).
        junction_position = self._signal_position.get(signal_id, trigger_position)
        clear_position = junction_position + CLEAR_DISTANCE_M + 40.0  # + cross-junction span

        # If this tls is already preempted (e.g. re-triggered by a
        # second event before release), extend rather than shorten the
        # active window, and don't log a redundant ON event.
        already_active = tls_id in self._active
        self._active[tls_id] = max(clear_position, self._active.get(tls_id, clear_position))

        if not already_active:
            self.log.append({
                "sim_time": sim_time,
                "event": "PREEMPT_ON",
                "tls_id": tls_id,
                "signal_id": signal_id,
                "case": case,
                "trigger_position_m": trigger_position,
                "junction_position_m": junction_position,
            })

    def _maybe_release(self, tls_id: str, sim_time: float, ambulance_distance: float):
        clear_position = self._active.get(tls_id)
        if clear_position is not None and ambulance_distance >= clear_position:
            tc_tls.setProgram(tls_id, self._original_program.get(tls_id, "0"))
            del self._active[tls_id]
            self.log.append({
                "sim_time": sim_time,
                "event": "PREEMPT_OFF",
                "tls_id": tls_id,
            })

    def run(self, cfg_path: str, gui: bool = False, max_steps: int = 3600) -> Dict:
        sumo_binary = "sumo-gui" if gui else "sumo"
        traci.start([sumo_binary, "-c", cfg_path])

        signal_tls = self.network_meta["signal_tls"]
        pending = list(self.events)
        ambulance_departed = False
        ambulance_arrival_time: Optional[float] = None
        step = 0

        try:
            while step < max_steps:
                traci.simulationStep()
                step += 1
                sim_time = traci.simulation.getTime()

                vehicle_ids = traci.vehicle.getIDList()
                if self.ambulance_id in vehicle_ids:
                    ambulance_departed = True
                    distance = traci.vehicle.getDistance(self.ambulance_id)

                    while pending and distance >= pending[0]["position_m"]:
                        event = pending.pop(0)
                        if event["kind"] == "ADVISORY":
                            self.log.append({
                                "sim_time": sim_time,
                                "event": "ADVISORY",
                                "signal_ids": event["signal_ids"],
                                "case": event["case"],
                                "position_m": event["position_m"],
                            })
                            continue

                        for signal_id in event["signal_ids"]:
                            tls_id = signal_tls[signal_id]
                            self._preempt(tls_id, signal_id, sim_time, event["position_m"], event["case"])

                    for tls_id in list(self._active.keys()):
                        self._maybe_release(tls_id, sim_time, distance)

                elif ambulance_departed and ambulance_arrival_time is None:
                    ambulance_arrival_time = sim_time

                if ambulance_arrival_time is not None and traci.simulation.getMinExpectedNumber() == 0:
                    break
        finally:
            traci.close()

        return {
            "ambulance_arrival_time": ambulance_arrival_time,
            "events": self.log,
            "steps_run": step,
        }
