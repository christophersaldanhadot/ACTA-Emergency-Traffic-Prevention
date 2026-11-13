from services.route_match_service import RouteMatchService
from services.signal_service import SignalService

from .fixtures import build_overpass_elements, build_route_coordinates


def _build_signals_and_geometry():
    signals = SignalService._elements_to_signals(build_overpass_elements())
    match_service = RouteMatchService()
    geometry = match_service.create_route_geometry(build_route_coordinates())
    return match_service, signals, geometry


def _signal(signals, sid):
    return next(s for s in signals if s["id"] == sid)


def test_signal_on_route_a_to_b():
    """TEST 5 / TEST 6: an eastbound one-way signal aligned with the
    route direction is classified ON_ROUTE / A_TO_B."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.classify_signal(_signal(signals, 1001), geometry)

    assert result["on_route"] is True
    assert result["direction"] == "A_TO_B"
    assert result["match_confidence"] > 0.5


def test_signal_opposite_direction_discarded():
    """TEST 6: a westbound one-way carriageway near the same junction
    must be discarded as the opposite direction, NOT included just
    because it is geometrically close."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.classify_signal(_signal(signals, 1002), geometry)

    assert result["on_route"] is False
    assert result["direction"] == "B_TO_A"
    assert result["reason"] == "opposite_direction"


def test_signal_outside_route_discarded():
    """TEST 7: a signal ~222m from the route corridor is off-route
    regardless of any way tags."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.classify_signal(_signal(signals, 1003), geometry)

    assert result["on_route"] is False
    assert result["reason"] == "off_route"
    assert result["distance_from_route_meters"] > 35


def test_cross_street_signal_discarded():
    """TEST 8: a signal that sits geometrically on the route line but
    whose road runs perpendicular to it (a cross street) must not be
    accepted merely because 'nearest route point' distance is ~0."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.classify_signal(_signal(signals, 1004), geometry)

    assert result["on_route"] is False
    assert result["reason"] == "ambiguous_direction"


def test_signal_without_way_data_marked_unknown():
    """Section 6: if OSM doesn't give enough info to determine direction,
    the signal must be marked direction_unknown rather than guessed
    into the route."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.classify_signal(_signal(signals, 1005), geometry)

    assert result["on_route"] is False
    assert result["direction"] == "UNKNOWN"
    assert result["reason"] == "direction_unknown"


def test_duplicate_osm_nodes_deduplicated():
    """TEST 9: signal 1007 is an exact-coordinate duplicate of 1001 and
    must be collapsed, while signals that are merely close together
    (1001 vs 1002, different coordinates) must NOT be merged."""
    match_service, signals, geometry = _build_signals_and_geometry()
    deduped = match_service.deduplicate_signals(signals)

    ids = [s["id"] for s in deduped]
    assert 1007 not in ids  # collapsed into 1001
    assert 1001 in ids
    assert 1002 in ids  # distinct signal, must be preserved


def test_full_matching_orders_signals_a_to_b():
    """TEST 10 / TEST 11: on-route signals come back sorted by position
    along the route, and inter-signal distances are computed along the
    route (not straight-line / not by raw OSM id)."""
    match_service, signals, geometry = _build_signals_and_geometry()
    result = match_service.match_route_signals(signals, geometry)

    on_route_ids = [s["signal_id"] for s in result["on_route_signals"]]
    assert on_route_ids == [1001, 1006]

    positions = [s["position_on_route_meters"] for s in result["on_route_signals"]]
    assert positions == sorted(positions)

    inter_signal_distance = positions[1] - positions[0]
    assert inter_signal_distance > 0

    discarded_ids = {s["signal_id"] for s in result["discarded_signals"]}
    assert discarded_ids == {1002, 1003, 1004, 1005}
