import os
import shutil

import pytest
from fastapi.testclient import TestClient

import app as app_module

from .fixtures import build_route_coordinates


def _synthetic_signals_all_cases():
    """4 signals producing all 3 ACTA cases: 101->102 = 50m
    (IMMEDIATE_TRIGGER), 102->103 = 500m (WAYPOINT_TRIGGER), 103->104 =
    1250m (MULTIPLE_WAYPOINTS)."""
    return [
        {"signal_id": 101, "position_on_route_meters": 500.0, "direction": "A_TO_B",
         "on_route": True, "match_confidence": 0.95, "latitude": 12.9, "longitude": 77.495,
         "distance_from_route_meters": 2.0},
        {"signal_id": 102, "position_on_route_meters": 550.0, "direction": "A_TO_B",
         "on_route": True, "match_confidence": 0.95, "latitude": 12.9, "longitude": 77.496,
         "distance_from_route_meters": 2.0},
        {"signal_id": 103, "position_on_route_meters": 1050.0, "direction": "A_TO_B",
         "on_route": True, "match_confidence": 0.95, "latitude": 12.9, "longitude": 77.500,
         "distance_from_route_meters": 2.0},
        {"signal_id": 104, "position_on_route_meters": 2300.0, "direction": "A_TO_B",
         "on_route": True, "match_confidence": 0.95, "latitude": 12.9, "longitude": 77.510,
         "distance_from_route_meters": 2.0},
    ]


@pytest.fixture()
def client(monkeypatch):
    coords = build_route_coordinates()

    async def fake_get_route(self, origin, destination):
        return {"distance_meters": 1800, "duration_seconds": 200, "polyline": "fake"}

    async def fake_get_signals(self, coordinates, use_cache=True):
        return []  # not used: we monkeypatch match_route_signals directly below

    def fake_decode_polyline(polyline_str):
        return coords

    def fake_match_route_signals(self, signals, geometry):
        on_route = _synthetic_signals_all_cases()
        return {"on_route_signals": on_route, "discarded_signals": [], "all_signals": on_route}

    monkeypatch.setattr(app_module.RouteService, "get_route", fake_get_route)
    monkeypatch.setattr(app_module.SignalService, "get_signals", fake_get_signals)
    monkeypatch.setattr(app_module, "decode_polyline", fake_decode_polyline)
    monkeypatch.setattr(app_module.RouteMatchService, "match_route_signals", fake_match_route_signals)

    yield TestClient(app_module.app)

    for name in ("Sim Origin", "Plan Origin"):
        dest = "Sim Destination" if name == "Sim Origin" else "Plan Destination"
        shutil.rmtree(app_module._scenario_dir_for(name, dest), ignore_errors=True)


def test_simulate_end_to_end_runs_real_sumo(client):
    """Full stack: HTTP request -> ACTA plan -> SUMO network/route build
    -> TraCI preemption run -> report, using the REAL sumo/netconvert
    binaries (not mocked)."""
    response = client.post(
        "/simulate",
        json={
            "origin": "Sim Origin",
            "destination": "Sim Destination",
            "profile": "Urban",
            "compare": False,
            "end_time": 300,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert os.path.exists(body["net_file"])
    assert os.path.exists(body["route_file"])
    assert os.path.exists(body["sumocfg_file"])
    assert body["with_preemption_arrival_s"] is not None

    events = body["events"]
    assert any(e["event"] == "PREEMPT_ON" and e.get("case") == "DISPATCH" for e in events)
    assert any(e["event"] == "PREEMPT_ON" and e.get("case") == "IMMEDIATE_TRIGGER" for e in events)
    assert any(e["event"] == "PREEMPT_ON" and e.get("case") == "WAYPOINT_TRIGGER" for e in events)
    assert any(e["event"] == "PREEMPT_ON" and e.get("case") == "MULTIPLE_WAYPOINTS" for e in events)
    assert any(e["event"] == "PREEMPT_OFF" for e in events)

    assert body["plan"]["signals"][0]["signal_id"] == 101


def test_simulate_plan_end_to_end_no_google_or_overpass_calls(client):
    """/simulate-plan must work from a plan JSON alone, with NO calls to
    RouteService/SignalService/RouteMatchService at all -- this is what
    guarantees the dashboard's simulation always matches exactly what's
    on screen, and lets a plan be replayed without re-hitting
    Google/Overpass."""
    plan = {
        "origin": "Plan Origin",
        "destination": "Plan Destination",
        "profile": "Urban",
        "route_distance_meters": 1800.0,
        "signals": _synthetic_signals_all_cases()[:3],
        "discarded_signals": [],
        "acta_plan": [
            {"from_signal": 101, "to_signal": 102, "distance_meters": 50.0, "case": "IMMEDIATE_TRIGGER"},
            {"from_signal": 102, "to_signal": 103, "distance_meters": 500.0, "case": "WAYPOINT_TRIGGER",
             "waypoint_meters": 550.0},
        ],
    }

    response = client.post(
        "/simulate-plan",
        json={"plan": plan, "compare": False, "end_time": 300},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert os.path.exists(body["net_file"])
    assert body["plan"]["origin"] == "Plan Origin"

    events = body["events"]
    assert any(e["event"] == "PREEMPT_ON" and e.get("case") == "IMMEDIATE_TRIGGER" for e in events)


def test_simulate_plan_empty_signals_returns_422(client):
    plan = {
        "origin": "X", "destination": "Y", "route_distance_meters": 100.0,
        "signals": [], "discarded_signals": [], "acta_plan": [],
    }
    response = client.post("/simulate-plan", json={"plan": plan})
    assert response.status_code == 422


def test_simulate_plan_compare_runs_calibrated_ego_car_baseline(client):
    """When compare=true, the "without preemption" baseline must come
    from a REAL SUMO run of ego_car (vClass="passenger", speedFactor
    calibrated from the plan's Google Routes timing) with preemption
    never applied -- not just Google's live ETA echoed straight back.
    Google's own number is still surfaced for reference/validation via
    calibration_delta_s."""
    plan = {
        "origin": "Plan Origin",
        "destination": "Plan Destination",
        "route_distance_meters": 1800.0,
        "route_static_duration_seconds": 300.0,
        "route_duration_in_traffic_seconds": 4380.0,
        "signals": _synthetic_signals_all_cases()[:3],
        "discarded_signals": [],
        "acta_plan": [
            {"from_signal": 101, "to_signal": 102, "distance_meters": 50.0, "case": "IMMEDIATE_TRIGGER"},
            {"from_signal": 102, "to_signal": 103, "distance_meters": 500.0, "case": "WAYPOINT_TRIGGER",
             "waypoint_meters": 550.0},
        ],
    }

    response = client.post(
        "/simulate-plan",
        json={"plan": plan, "compare": True, "end_time": 300},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["baseline_source"] == "sumo_calibrated_ego_car"
    assert body["without_preemption_arrival_s"] is not None
    assert body["without_preemption_arrival_s"] > 0
    # A real (calibrated, congested) SUMO run, not Google's raw number.
    assert body["without_preemption_arrival_s"] != 4380.0
    assert body["google_live_traffic_eta_s"] == 4380.0
    assert body["calibration_delta_s"] == round(
        body["without_preemption_arrival_s"] - 4380.0, 1
    )
    assert body["time_saved_s"] == round(
        body["without_preemption_arrival_s"] - body["with_preemption_arrival_s"], 1
    )
    assert 0 < body["ego_speed_factor"] <= 1.0
