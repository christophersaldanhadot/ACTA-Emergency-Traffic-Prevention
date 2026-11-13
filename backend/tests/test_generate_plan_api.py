import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.polyline_decoder import decode_polyline

from .fixtures import build_overpass_elements, build_route_coordinates


def _fake_encoded_polyline():
    # Doesn't need to decode to build_route_coordinates() exactly; we
    # monkeypatch decode_polyline itself for full control instead.
    return "fake"


@pytest.fixture()
def client(monkeypatch):
    coords = build_route_coordinates()

    async def fake_get_route(self, origin, destination):
        return {
            "distance_meters": 2700,
            "duration_seconds": 400,
            "polyline": _fake_encoded_polyline(),
        }

    async def fake_get_signals(self, coordinates, use_cache=True):
        from services.signal_service import SignalService
        return SignalService._elements_to_signals(build_overpass_elements())

    def fake_decode_polyline(polyline_str):
        return coords

    monkeypatch.setattr(app_module.RouteService, "get_route", fake_get_route)
    monkeypatch.setattr(app_module.SignalService, "get_signals", fake_get_signals)
    monkeypatch.setattr(app_module, "decode_polyline", fake_decode_polyline)

    return TestClient(app_module.app)


def test_generate_full_plan_end_to_end(client):
    """TEST 16: full pipeline via the API -- route -> signals -> matching
    -> ordered A->B signals -> ACTA plan -- with no live network calls."""
    response = client.post(
        "/generate-full-plan",
        json={
            "origin": "Christ University Kengeri Campus",
            "destination": "Rajarajeshwari Nagar Metro Station",
            "profile": "Urban",
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["route_distance_meters"] == 2700
    signal_ids = [s["signal_id"] for s in body["signals"]]
    assert signal_ids == [1001, 1006]

    discarded_ids = {s["signal_id"] for s in body["discarded_signals"]}
    assert discarded_ids == {1002, 1003, 1004, 1005}

    assert len(body["acta_plan"]) == 1
    assert body["acta_plan"][0]["from_signal"] == 1001
    assert body["acta_plan"][0]["to_signal"] == 1006


def test_generate_full_plan_invalid_profile(client):
    response = client.post(
        "/generate-full-plan",
        json={
            "origin": "A",
            "destination": "B",
            "profile": "NotAProfile",
        },
    )
    assert response.status_code == 400


def test_generate_full_plan_route_failure_returns_502(client, monkeypatch):
    async def failing_get_route(self, origin, destination):
        raise Exception("Google Routes API unavailable")

    monkeypatch.setattr(app_module.RouteService, "get_route", failing_get_route)

    response = client.post(
        "/generate-full-plan",
        json={"origin": "A", "destination": "B", "profile": "Urban"},
    )
    assert response.status_code == 502


def test_generate_full_plan_signal_service_down_degrades_gracefully(client, monkeypatch):
    """If OSM/Overpass is completely unavailable, the endpoint should
    still return a valid (empty-signal) plan instead of crashing."""

    async def failing_get_signals(self, coordinates, use_cache=True):
        raise Exception("all overpass mirrors down")

    monkeypatch.setattr(app_module.SignalService, "get_signals", failing_get_signals)

    response = client.post(
        "/generate-full-plan",
        json={
            "origin": "Christ University Kengeri Campus",
            "destination": "Rajarajeshwari Nagar Metro Station",
            "profile": "Urban",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["signals"] == []
    assert body["acta_plan"] == []
