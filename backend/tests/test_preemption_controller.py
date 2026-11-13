import os
import sys

import pytest

SIMULATION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "simulation"))
if SIMULATION_DIR not in sys.path:
    sys.path.insert(0, SIMULATION_DIR)

import preemption_controller as pc  # noqa: E402


def _plan_with_three_signals():
    """Signal 201 at 500m, 202 at 1500m (1000m gap -> IMMEDIATE_TRIGGER
    under the Urban profile), 203 at 3000m (1500m gap -> WAYPOINT_TRIGGER)."""
    signals = [
        {"signal_id": 201, "position_on_route_meters": 500.0},
        {"signal_id": 202, "position_on_route_meters": 1500.0},
        {"signal_id": 203, "position_on_route_meters": 3000.0},
    ]
    acta_plan = [
        {"from_signal": 201, "to_signal": 202, "distance_meters": 1000.0, "case": "IMMEDIATE_TRIGGER"},
        {"from_signal": 202, "to_signal": 203, "distance_meters": 1500.0, "case": "WAYPOINT_TRIGGER",
         "waypoint_meters": 2500.0},
    ]
    return {
        "signals": signals,
        "acta_plan": acta_plan,
        "route_distance_meters": 3500.0,
        "discarded_signals": [],
    }


def _network_meta():
    return {
        "signal_tls": {201: "n_sig_201", 202: "n_sig_202", 203: "n_sig_203"},
    }


def test_signal_position_lookup_built_from_plan():
    controller = pc.PreemptionController(_network_meta(), _plan_with_three_signals())
    assert controller._signal_position == {201: 500.0, 202: 1500.0, 203: 3000.0}


def test_trigger_events_fire_at_upstream_signal_not_downstream(monkeypatch):
    """IMMEDIATE_TRIGGER must fire when the ambulance reaches the
    UPSTREAM signal (201 @ 500m), not the downstream one being preempted."""
    controller = pc.PreemptionController(_network_meta(), _plan_with_three_signals())
    immediate_event = next(e for e in controller.events if e["case"] == "IMMEDIATE_TRIGGER")
    assert immediate_event["position_m"] == 500.0
    assert immediate_event["signal_ids"] == [202]


def test_release_position_uses_downstream_signals_own_position(monkeypatch):
    """REGRESSION for the bug where clear_position was computed from the
    TRIGGER's fire position instead of the preempted signal's own real
    position. For IMMEDIATE_TRIGGER, signal 202 (at 1500m) is preempted
    when the ambulance reaches signal 201 (at 500m) -- release must wait
    until the ambulance is actually near 1500m, NOT near 500m+65m."""
    controller = pc.PreemptionController(_network_meta(), _plan_with_three_signals())

    monkeypatch.setattr(pc.tc_tls, "getProgram", lambda tls_id: "0")
    monkeypatch.setattr(pc.tc_tls, "getControlledLinks", lambda tls_id: [])
    monkeypatch.setattr(pc.tc_tls, "setProgramLogic", lambda tls_id, logic: None)
    monkeypatch.setattr(pc.tc_tls, "setProgram", lambda tls_id, program: None)

    # Fire the IMMEDIATE_TRIGGER event: ambulance is at 500m (signal 201),
    # preempting signal 202 (whose real position is 1500m).
    controller._preempt("n_sig_202", 202, sim_time=10.0, trigger_position=500.0, case="IMMEDIATE_TRIGGER")

    clear_position = controller._active["n_sig_202"]

    # The old (buggy) formula would give 500 + 25 + 40 = 565.
    buggy_clear_position = 500.0 + pc.CLEAR_DISTANCE_M + 40.0
    assert clear_position != buggy_clear_position

    # The correct formula uses signal 202's OWN position (1500m).
    expected = 1500.0 + pc.CLEAR_DISTANCE_M + 40.0
    assert clear_position == expected

    # At ambulance distance 565m (where the OLD bug would have released
    # it), the junction must still be actively preempted.
    controller._maybe_release("n_sig_202", sim_time=30.0, ambulance_distance=565.0)
    assert "n_sig_202" in controller._active, "released far too early -- regression of the clear-position bug"

    # Only once the ambulance is actually near signal 202's real
    # position should it release.
    controller._maybe_release("n_sig_202", sim_time=90.0, ambulance_distance=expected)
    assert "n_sig_202" not in controller._active


def test_waypoint_trigger_release_uses_target_signal_position(monkeypatch):
    """WAYPOINT_TRIGGER fires at a waypoint BEFORE the target signal
    (queueBuffer metres early) -- release must still be keyed to the
    target signal's real position, not the (earlier) waypoint."""
    controller = pc.PreemptionController(_network_meta(), _plan_with_three_signals())

    monkeypatch.setattr(pc.tc_tls, "getProgram", lambda tls_id: "0")
    monkeypatch.setattr(pc.tc_tls, "getControlledLinks", lambda tls_id: [])
    monkeypatch.setattr(pc.tc_tls, "setProgramLogic", lambda tls_id, logic: None)
    monkeypatch.setattr(pc.tc_tls, "setProgram", lambda tls_id, program: None)

    # Waypoint at 2500m triggers preemption of signal 203 (real position 3000m).
    controller._preempt("n_sig_203", 203, sim_time=50.0, trigger_position=2500.0, case="WAYPOINT_TRIGGER")

    expected = 3000.0 + pc.CLEAR_DISTANCE_M + 40.0
    assert controller._active["n_sig_203"] == expected


def test_repeated_preempt_does_not_shorten_active_window(monkeypatch):
    """If the same tls is preempted twice before release (e.g. dispatch
    then re-triggered), the active window must extend, never shrink,
    and no duplicate PREEMPT_ON log entry should be added."""
    controller = pc.PreemptionController(_network_meta(), _plan_with_three_signals())

    monkeypatch.setattr(pc.tc_tls, "getProgram", lambda tls_id: "0")
    monkeypatch.setattr(pc.tc_tls, "getControlledLinks", lambda tls_id: [])
    monkeypatch.setattr(pc.tc_tls, "setProgramLogic", lambda tls_id, logic: None)
    monkeypatch.setattr(pc.tc_tls, "setProgram", lambda tls_id, program: None)

    controller._preempt("n_sig_201", 201, sim_time=1.0, trigger_position=0.0, case="DISPATCH")
    first_clear = controller._active["n_sig_201"]

    # Re-triggered with a position that would imply an EARLIER clear if
    # naively overwritten -- must not shrink the window.
    controller._preempt("n_sig_201", 201, sim_time=5.0, trigger_position=0.0, case="DISPATCH")
    second_clear = controller._active["n_sig_201"]

    assert second_clear >= first_clear
    on_events = [e for e in controller.log if e["event"] == "PREEMPT_ON"]
    assert len(on_events) == 1, "duplicate PREEMPT_ON should not be logged while already active"
