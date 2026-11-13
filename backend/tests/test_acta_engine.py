import pytest

from services.acta_engine import ActaEngine, IMMEDIATE_THRESHOLD_M, MULTI_WAYPOINT_THRESHOLD_M


@pytest.fixture()
def engine():
    return ActaEngine()


def test_immediate_trigger_at_boundary(engine):
    """d <= 100m -> IMMEDIATE_TRIGGER, including exactly at the boundary."""
    result = engine.evaluate("Urban", [IMMEDIATE_THRESHOLD_M])
    assert result[0]["decision"] == "IMMEDIATE"

    result = engine.evaluate("Urban", [50])
    assert result[0]["decision"] == "IMMEDIATE"


def test_waypoint_trigger_middle_band(engine):
    """100m < d < 1000m -> single WAYPOINT_TRIGGER before the next signal."""
    result = engine.evaluate("Urban", [101])
    assert result[0]["decision"] == "WAYPOINT"

    result = engine.evaluate("Urban", [600])
    assert result[0]["decision"] == "WAYPOINT"
    assert 0 <= result[0]["waypoint"] < 600

    result = engine.evaluate("Urban", [999])
    assert result[0]["decision"] == "WAYPOINT"


def test_multiple_waypoints_at_and_above_boundary(engine):
    """d >= 1000m -> MULTIPLE_WAYPOINTS, including exactly at the boundary."""
    result = engine.evaluate("Urban", [MULTI_WAYPOINT_THRESHOLD_M])
    assert result[0]["decision"] == "MULTIPLE_WAYPOINTS"

    result = engine.evaluate("Urban", [3500])
    assert result[0]["decision"] == "MULTIPLE_WAYPOINTS"
    waypoints = result[0]["waypoints"]
    assert len(waypoints) >= 2
    assert waypoints == sorted(waypoints)
    assert all(0 < w <= 3500 for w in waypoints)


def test_case_boundaries_are_profile_independent(engine):
    """Per the project architecture spec, the 100m/1000m case boundaries
    are fixed, NOT profile-dependent -- only queueBuffer/waypointSpacing
    (which affect waypoint placement, not case selection) vary by
    profile."""
    for profile in ("Urban", "CBD", "Highway", "Rural"):
        assert engine.evaluate(profile, [50])[0]["decision"] == "IMMEDIATE"
        assert engine.evaluate(profile, [500])[0]["decision"] == "WAYPOINT"
        assert engine.evaluate(profile, [5000])[0]["decision"] == "MULTIPLE_WAYPOINTS"


def test_unknown_profile_raises_value_error(engine):
    with pytest.raises(ValueError):
        engine.evaluate("NotAProfile", [500])


def test_build_plan_from_signals_uses_real_signal_ids(engine):
    """ACTA plan built from ordered on-route signals must reference the
    real OSM signal ids, not placeholder letters."""
    ordered_signals = [
        {"signal_id": 1001, "position_on_route_meters": 1000.0},
        {"signal_id": 1006, "position_on_route_meters": 1080.0},
    ]
    plan = engine.build_plan_from_signals("Urban", ordered_signals)

    assert len(plan) == 1
    assert plan[0]["from_signal"] == 1001
    assert plan[0]["to_signal"] == 1006
    assert plan[0]["distance_meters"] == 80.0
    assert plan[0]["case"] == "IMMEDIATE_TRIGGER"


def test_build_plan_from_signals_segment_telemetry_is_internally_consistent(engine):
    """Regression test for the reported hover-card bug: a single
    segment's `case`/`case_type` label must agree with its own
    breakdown fields, and waypoint arrays must never appear on a
    segment that didn't itself qualify for them (e.g. a short ~123m
    segment showing a MULTIPLE_WAYPOINTS array, or a segment labelled
    one case while its telemetry breakdown implies another)."""
    ordered_signals = [
        {"signal_id": "A", "position_on_route_meters": 0.0},
        {"signal_id": "B", "position_on_route_meters": 123.0},  # short segment
        {"signal_id": "C", "position_on_route_meters": 123.0 + 1400.0},  # long segment
    ]

    plan = engine.build_plan_from_signals("Urban", ordered_signals)
    short_seg, long_seg = plan[0], plan[1]

    # The short 123m segment: exact-spec case_type must reflect L < MT
    # (MERGE_TRIGGER by default thresholds), and it must NOT carry any
    # waypoint array of any kind, legacy or exact-spec.
    assert short_seg["distance_meters"] == 123.0
    assert short_seg["case_type"] == "MERGE_TRIGGER"
    assert "waypoints_meters" not in short_seg
    assert "spec_waypoints" not in short_seg
    # Legacy classifier may still label this WAYPOINT_TRIGGER under its
    # own 100m/1000m bands -- that's a separate legacy field, but even
    # it must not carry a waypoints_meters ARRAY for this short segment.
    assert "waypoints_meters" not in short_seg

    # Every segment's own case/case_type/target_speed/waypoints must be
    # self-consistent -- computed from that segment's own distance only.
    for seg in plan:
        if seg["case_type"] == "WAYPOINT_TRIGGER":
            assert seg["distance_meters"] > 1000.0
            assert "spec_waypoints" in seg
        else:
            assert "spec_waypoints" not in seg

    # The long (>1000m) segment must carry its own waypoints and a sane
    # (non-absurdly-low) target speed -- not a fixed 60s/short-distance
    # artifact.
    assert long_seg["distance_meters"] > 1000.0
    assert long_seg["case_type"] == "WAYPOINT_TRIGGER"
    assert "spec_waypoints" in long_seg
    assert long_seg["target_speed"]["kmh"] >= 20.0  # guard rail floor


def test_target_speed_never_absurdly_low_for_short_segments(engine):
    """A short segment must not produce a physically nonsensical
    ~7 km/h target speed just because the default 60s traversal-time
    target doesn't fit it; the engine clamps the effective traversal
    time so v_target never falls below a sane emergency-corridor
    minimum."""
    telemetry = engine.compute_segment_telemetry(123.0, t_target_s=60.0)
    assert telemetry["target_speed"]["kmh"] >= 20.0
    assert telemetry["parameters"]["effective_t_target_s"] < 60.0


def test_build_plan_from_signals_fewer_than_two_signals(engine):
    """ACTA receiving fewer than two valid signals should not crash --
    the caller (app.py) is expected to short-circuit, but the engine
    itself should also behave sensibly (empty plan)."""
    plan = engine.build_plan_from_signals(
        "Urban", [{"signal_id": 1001, "position_on_route_meters": 1000.0}]
    )
    assert plan == []
