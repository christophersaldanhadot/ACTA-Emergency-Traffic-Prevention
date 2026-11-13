from services.polyline_decoder import decode_polyline
from services.route_match_service import RouteMatchService

from .fixtures import (
    GOOGLE_POLYLINE_SAMPLE,
    GOOGLE_POLYLINE_EXPECTED,
    build_route_coordinates,
)


def test_google_route_decoding():
    """TEST 1: decoding a known Google-encoded polyline yields the
    documented reference coordinates."""
    decoded = decode_polyline(GOOGLE_POLYLINE_SAMPLE)
    assert len(decoded) == len(GOOGLE_POLYLINE_EXPECTED)
    for actual, expected in zip(decoded, GOOGLE_POLYLINE_EXPECTED):
        assert actual["lat"] == expected["lat"]
        assert actual["lng"] == expected["lng"]


def test_route_geometry_length():
    """TEST 2: route geometry length is the sum of haversine segment
    distances and roughly matches the straight-line distance for our
    (near-straight) synthetic east-west route."""
    coordinates = build_route_coordinates()
    match_service = RouteMatchService()
    geometry = match_service.create_route_geometry(coordinates)

    assert len(geometry["segments"]) == len(coordinates) - 1

    expected_straight_line = match_service.haversine(
        coordinates[0]["lat"], coordinates[0]["lng"],
        coordinates[-1]["lat"], coordinates[-1]["lng"],
    )

    # A straight route's polyline length should be very close to the
    # straight-line distance between its endpoints.
    assert abs(geometry["length"] - expected_straight_line) < 1.0
