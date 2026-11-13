import httpx
import pytest

from services.route_service import RouteService


@pytest.mark.asyncio
async def test_get_route_parses_whole_second_duration():
    """Real Google Routes API responses can use whole-second durations
    like "1234s"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [{
                "duration": "1234s",
                "distanceMeters": 15771,
                "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"},
            }]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("A", "B")

    assert result["duration_seconds"] == 1234
    assert result["distance_meters"] == 15771
    assert result["polyline"] == "csvmAgd|wM{m@oqAwj@gpAgaAkvB"


@pytest.mark.asyncio
async def test_get_route_parses_fractional_second_duration():
    """REGRESSION: Google's protobuf Duration JSON encoding legally
    includes fractional seconds, e.g. "212.500s". A naive
    int(duration.replace('s','')) raises ValueError on this -- this
    must not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [{
                "duration": "212.500s",
                "distanceMeters": 900,
                "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"},
            }]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("A", "B")

    assert result["duration_seconds"] == 212  # round-half-to-even (Python's round())


@pytest.mark.asyncio
async def test_get_route_missing_api_key_raises_clear_error():
    service = RouteService()
    service.api_key = None
    with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_403_forbidden_gives_clear_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="API key not valid")

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "bad-key"
    with pytest.raises(Exception, match="403"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_429_rate_limit_gives_clear_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Quota exceeded")

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="quota"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_connection_error_gives_clear_message():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="Could not connect"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_timeout_gives_clear_message():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="timed out"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_empty_routes_array():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"routes": []})

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="no routes"):
        await service.get_route("Nowhere", "Nowhere else")


@pytest.mark.asyncio
async def test_get_route_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="malformed JSON"):
        await service.get_route("A", "B")


@pytest.mark.asyncio
async def test_get_route_sends_correct_request_shape():
    """The request body/headers must match the real Google Routes API v2
    contract -- this is the actual thing that makes it 'connect
    properly' once real credentials are used."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    await service.get_route("Origin Place", "Destination Place")

    assert captured["headers"]["x-goog-api-key"] == "the-key"
    assert "routes.polyline.encodedPolyline" in captured["headers"]["x-goog-fieldmask"]
    assert captured["json"]["origin"]["address"] == "Origin Place"
    assert captured["json"]["destination"]["address"] == "Destination Place"
    assert captured["json"]["travelMode"] == "DRIVE"
    # Hardcoded car routing, never bike/walk/transit.
    assert captured["json"]["travelMode"] == "DRIVE"
    # Real-time traffic conditions, not a static/typical-time estimate.
    assert captured["json"]["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"


@pytest.mark.asyncio
async def test_get_route_shortest_mode_uses_traffic_unaware_preference():
    """routing_mode='shortest' should request TRAFFIC_UNAWARE, the
    closest available Routes API proxy for a distance-minimizing path
    (the API has no first-class 'minimize distance' objective)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    result = await service.get_route("Origin Place", "Destination Place", routing_mode="shortest")

    assert captured["json"]["routingPreference"] == "TRAFFIC_UNAWARE"
    assert result["routing_mode"] == "shortest"
    assert result["traffic_aware"] is False


@pytest.mark.asyncio
async def test_get_route_rejects_unknown_routing_mode():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    with pytest.raises(ValueError, match="Unknown routing_mode"):
        await service.get_route("A", "B", routing_mode="scenic")


@pytest.mark.asyncio
async def test_get_route_returns_traffic_aware_duration_and_static_duration():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [{
                "duration": "4380s",  # traffic-aware (73 min)
                "staticDuration": "3600s",  # typical/free-flow (60 min)
                "distanceMeters": 15771,
                "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"},
            }]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("A", "B")

    assert result["duration_in_traffic_seconds"] == 4380
    assert result["static_duration_seconds"] == 3600
    assert result["duration_seconds"] == 4380  # back-compat alias, now traffic-aware
    assert result["traffic_aware"] is True
    assert result["source"] == "google_routes_api"


@pytest.mark.asyncio
async def test_get_route_requests_alternative_routes():
    """REGRESSION: without computeAlternativeRoutes=True, Google only
    returns a single route and 'fastest'/'shortest' can silently collapse
    onto the same path (same distance, near-identical ETAs)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    await service.get_route("A", "B")

    assert captured["json"]["computeAlternativeRoutes"] is True


@pytest.mark.asyncio
async def test_get_route_fastest_picks_lowest_duration_among_alternatives():
    """REGRESSION: 'fastest' must explicitly pick the min-duration route
    out of all alternatives, not just take routes[0]."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [
                {"duration": "1530s", "distanceMeters": 5620, "polyline": {"encodedPolyline": "wj}mAoeuxMw|Aw|Aw|Aw|A"}},
                {"duration": "1262s", "distanceMeters": 6100, "polyline": {"encodedPolyline": "_pvmAokmxM_|B_|B_|Bw|A"}},
            ]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("A", "B", routing_mode="fastest")

    assert result["duration_in_traffic_seconds"] == 1262
    assert result["distance_meters"] == 6100
    assert result["polyline"] == "_pvmAokmxM_|B_|B_|Bw|A"


@pytest.mark.asyncio
async def test_get_route_shortest_picks_lowest_distance_among_alternatives():
    """REGRESSION: 'shortest' must explicitly pick the min-distance route
    out of all alternatives, not just take routes[0]."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [
                {"duration": "1530s", "distanceMeters": 5620, "polyline": {"encodedPolyline": "wj}mAoeuxMw|Aw|Aw|Aw|A"}},
                {"duration": "1262s", "distanceMeters": 6100, "polyline": {"encodedPolyline": "_pvmAokmxM_|B_|B_|Bw|A"}},
            ]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("A", "B", routing_mode="shortest")

    assert result["distance_meters"] == 5620
    assert result["duration_in_traffic_seconds"] == 1530
    assert result["polyline"] == "wj}mAoeuxMw|Aw|Aw|Aw|A"


@pytest.mark.asyncio
async def test_get_route_fastest_and_shortest_diverge_given_same_alternatives():
    """REGRESSION for the reported bug: with real alternatives present,
    'fastest' and 'shortest' must select different routes (different
    distance AND non-inverted ETAs), not collapse onto the same
    5.62km / swapped-ETA result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [
                {"duration": "1530s", "distanceMeters": 5620, "polyline": {"encodedPolyline": "wj}mAoeuxMw|Aw|Aw|Aw|A"}},
                {"duration": "1262s", "distanceMeters": 6100, "polyline": {"encodedPolyline": "_pvmAokmxM_|B_|B_|Bw|A"}},
            ]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"

    fastest = await service.get_route("A", "B", routing_mode="fastest")
    shortest = await service.get_route("A", "B", routing_mode="shortest")

    assert fastest["distance_meters"] != shortest["distance_meters"]
    assert fastest["duration_in_traffic_seconds"] < shortest["duration_in_traffic_seconds"]


@pytest.mark.asyncio
async def test_get_route_sends_india_region_bias():
    """The request must bias fuzzy address geocoding toward India
    (regionCode), so an ambiguous input like 'sda hospital' is more
    likely to resolve to a same-named place in India rather than
    elsewhere in the world."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    await service.get_route("Origin Place", "Destination Place")

    assert captured["json"]["regionCode"] == "IN"


@pytest.mark.asyncio
async def test_get_route_rejects_endpoint_outside_bengaluru_metro():
    """REGRESSION for the reported bug: a fuzzy input like 'sda
    hospital' can geocode to a same-named place hundreds/thousands of
    km away, producing a cross-country route. A resolved route
    endpoint far outside the Bengaluru metro bias radius must be
    rejected with a clear error, never silently passed through to
    Overpass."""

    # Mumbai, ~840km from central Bangalore -- well outside the 50km
    # radius.
    far_away_polyline = "_xlsBs|x{L"  # single point near Mumbai (19.0760, 72.8777)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [{
                "duration": "36000s",
                "distanceMeters": 840000,
                "polyline": {"encodedPolyline": far_away_polyline},
            }]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    with pytest.raises(Exception, match="metro service area"):
        await service.get_route("sda hospital", "Bangalore")


@pytest.mark.asyncio
async def test_get_route_accepts_endpoints_within_bengaluru_metro():
    """A route whose resolved endpoints are within the metro bias
    radius must succeed normally -- the check should not be so strict
    it rejects legitimate local routes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "routes": [{
                "duration": "480s",
                "distanceMeters": 2700,
                "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"},
            }]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "test-key"
    result = await service.get_route("Christ University Kengeri", "RR Nagar Metro")

    assert result["source"] == "google_routes_api"
    assert result["distance_meters"] == 2700


@pytest.mark.asyncio
async def test_get_route_uses_place_id_when_provided():
    """When the frontend's Places Autocomplete resolved a specific
    place, its place_id must be sent as Routes API's `placeId` waypoint
    field instead of re-geocoding the free-text address string."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    await service.get_route(
        "SDA Hospital",
        "RR Nagar Metro Station",
        origin_place_id="ChIJexampleOriginPlaceId",
        destination_place_id="ChIJexampleDestPlaceId",
    )

    assert captured["json"]["origin"] == {"placeId": "ChIJexampleOriginPlaceId"}
    assert captured["json"]["destination"] == {"placeId": "ChIJexampleDestPlaceId"}


@pytest.mark.asyncio
async def test_get_route_falls_back_to_address_when_no_place_id():
    """Without a place_id (e.g. manually typed address, never run
    through Autocomplete), the free-text address string is still used
    -- place IDs are an enhancement, not a hard requirement."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={
            "routes": [{"duration": "10s", "distanceMeters": 10, "polyline": {"encodedPolyline": "csvmAgd|wM{m@oqAwj@gpAgaAkvB"}}]
        })

    service = RouteService(transport=httpx.MockTransport(handler))
    service.api_key = "the-key"
    await service.get_route("Origin Place", "Destination Place")

    assert captured["json"]["origin"] == {"address": "Origin Place"}
    assert captured["json"]["destination"] == {"address": "Destination Place"}


@pytest.mark.asyncio
async def test_get_route_mock_mode_when_no_api_key(monkeypatch):
    """ACTA_MOCK_ROUTES=1 lets the pipeline run end-to-end with no
    GOOGLE_MAPS_API_KEY configured, for offline demos -- but only when
    explicitly opted in, never silently."""
    import services.route_service as route_service_module

    monkeypatch.setattr(route_service_module, "MOCK_ROUTES_ENABLED", True)
    service = RouteService()
    service.api_key = None

    result = await service.get_route("Somewhere", "Somewhere Else")

    assert result["source"] == "mock"
    assert result["duration_in_traffic_seconds"] > 0
    assert result["distance_meters"] > 0


@pytest.mark.asyncio
async def test_get_route_missing_api_key_without_mock_mode_still_raises(monkeypatch):
    import services.route_service as route_service_module

    monkeypatch.setattr(route_service_module, "MOCK_ROUTES_ENABLED", False)
    service = RouteService()
    service.api_key = None

    with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY"):
        await service.get_route("A", "B")
