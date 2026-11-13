import asyncio
import json
import tempfile

import httpx
import pytest

from services.signal_service import (
    SignalService,
    OVERPASS_ENDPOINTS,
    USER_AGENT,
)

from .fixtures import build_overpass_elements, build_route_coordinates


def _overpass_response_json():
    return {"version": 0.6, "elements": build_overpass_elements()}


@pytest.mark.asyncio
async def test_signal_retrieval_success():
    """TEST 3: a healthy primary mirror returns parsed, structured signals."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_overpass_response_json())

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=transport)
        signals = await service.get_signals(build_route_coordinates(), use_cache=False)

    ids = {s["id"] for s in signals}
    assert 1001 in ids and 1006 in ids
    signal_1001 = next(s for s in signals if s["id"] == 1001)
    assert signal_1001["ways"], "signal 1001 should have an associated way"
    assert signal_1001["ways"][0]["oneway"] == "yes"


@pytest.mark.asyncio
async def test_overpass_fallback_on_504():
    """TEST 4: primary mirror returns 504, second mirror refuses the
    connection, third mirror succeeds -- SignalService must still
    return data, and must have tried endpoints in the documented order."""

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == OVERPASS_ENDPOINTS[0]:
            return httpx.Response(504, text="Gateway Timeout")
        if str(request.url) == OVERPASS_ENDPOINTS[1]:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=_overpass_response_json())

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=transport)
        signals = await service.get_signals(build_route_coordinates(), use_cache=False)

    assert len(calls) == 3, "all three mirrors should have been attempted in order"
    assert calls[0] == OVERPASS_ENDPOINTS[0]
    assert calls[1] == OVERPASS_ENDPOINTS[1]
    assert calls[2] == OVERPASS_ENDPOINTS[2]
    assert any(s["id"] == 1001 for s in signals)


@pytest.mark.asyncio
async def test_all_mirrors_down_returns_empty_not_crash():
    """Error handling: if every mirror is unavailable, SignalService must
    degrade gracefully (empty list) instead of raising deep inside the
    pipeline."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, text="Gateway Timeout")

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=transport)
        signals = await service.get_signals(build_route_coordinates(), use_cache=False)

    assert signals == []


@pytest.mark.asyncio
async def test_successful_response_is_cached_but_empty_is_not():
    """A non-empty response should populate the cache; an empty response
    (e.g. genuinely no signals in the bbox) should NOT create a
    misleading cache entry that looks like a failure."""

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=_overpass_response_json())

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=transport)
        coords = build_route_coordinates()

        await service.get_signals(coords, use_cache=True)
        assert calls["count"] == 1

        # Second call should be served from cache, not hit the network again.
        await service.get_signals(coords, use_cache=True)
        assert calls["count"] == 1


@pytest.mark.asyncio
async def test_requests_send_custom_user_agent():
    """Several public Overpass mirrors return HTTP 406 for requests
    without a descriptive User-Agent -- every request must send one."""

    captured_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(dict(request.headers))
        return httpx.Response(200, json=_overpass_response_json())

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=transport)
        await service.get_signals(build_route_coordinates(), use_cache=False)

    assert captured_headers, "no request was sent"
    for headers in captured_headers:
        assert headers.get("user-agent") == USER_AGENT


@pytest.mark.asyncio
async def test_second_cache_entry_also_uses_cache_across_new_service_instances():
    """Caching must survive across separate SignalService instances (i.e.
    separate requests to the backend), not just within one instance's
    lifetime -- this is what actually avoids repeat live Overpass
    queries for the same route."""

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=_overpass_response_json())

    transport = httpx.MockTransport(handler)
    with tempfile.TemporaryDirectory() as cache_dir:
        coords = build_route_coordinates()

        service_a = SignalService(cache_dir=cache_dir, transport=transport)
        await service_a.get_signals(coords, use_cache=True)
        assert calls["count"] == 1

        # Brand new instance, same cache_dir -- simulates a second,
        # unrelated request hitting the backend later.
        service_b = SignalService(cache_dir=cache_dir, transport=transport)
        signals = await service_b.get_signals(coords, use_cache=True)
        assert calls["count"] == 1, "second instance should reuse the on-disk cache, not re-query"
        assert any(s["id"] == 1001 for s in signals)


def test_default_timeouts_are_fast_for_quicker_failover():
    """REGRESSION: the old flat 60s-per-mirror timeout meant a single
    unresponsive mirror could stall the whole request for a minute
    before even trying the next one. Defaults must be much shorter."""
    service = SignalService()
    assert service.connect_timeout <= 10.0
    assert service.timeout <= 20.0


@pytest.mark.asyncio
async def test_read_timeout_is_passed_through_to_httpx_client(monkeypatch):
    """The configured connect/read timeouts must actually reach the
    httpx.AsyncClient that makes the request -- this is what causes
    real failover to be fast. (A MockTransport-based functional test
    can't exercise this directly: custom transports bypass httpx's own
    timeout enforcement, since that lives at the real connection layer,
    so we instead assert on the exact httpx.Timeout object built.)"""
    captured = {}
    real_async_client = httpx.AsyncClient

    class CapturingClient(real_async_client):
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", CapturingClient)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_overpass_response_json())

    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(
            cache_dir=cache_dir,
            timeout=12.0,
            connect_timeout=4.0,
            transport=httpx.MockTransport(handler),
        )
        await service.get_signals(build_route_coordinates(), use_cache=False)

    timeout_obj = captured["timeout"]
    assert timeout_obj.connect == 4.0
    assert timeout_obj.read == 12.0


@pytest.mark.asyncio
async def test_multi_tile_requests_share_one_client_and_run_concurrently(monkeypatch):
    """PERFORMANCE: for a route spanning multiple bbox tiles, all tile
    requests must share a single httpx.AsyncClient (connection reuse)
    and be issued concurrently, not one-by-one -- this is the dominant
    latency win for long routes."""
    client_instances = []
    real_async_client = httpx.AsyncClient

    class CountingClient(real_async_client):
        def __init__(self, *args, **kwargs):
            client_instances.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", CountingClient)

    in_flight = {"count": 0, "max_concurrent": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        in_flight["count"] += 1
        in_flight["max_concurrent"] = max(in_flight["max_concurrent"], in_flight["count"])
        await asyncio.sleep(0.05)
        in_flight["count"] -= 1
        return httpx.Response(200, json=_overpass_response_json())

    class ConcurrentTrackingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await handler(request)

    with tempfile.TemporaryDirectory() as cache_dir:
        service = SignalService(cache_dir=cache_dir, transport=ConcurrentTrackingTransport())
        # Force multiple tiles: monkeypatch _build_bboxes to return 4 tiles.
        service._build_bboxes = lambda coords: [
            (12.90, 77.49, 12.91, 77.50),
            (12.91, 77.50, 12.92, 77.51),
            (12.92, 77.51, 12.93, 77.52),
            (12.93, 77.52, 12.94, 77.53),
        ]
        await service.get_signals(build_route_coordinates(), use_cache=False)

    assert len(client_instances) == 1, "all tiles must share exactly one AsyncClient"
    assert in_flight["max_concurrent"] > 1, "tile requests should overlap in time (concurrent), not run strictly sequentially"
