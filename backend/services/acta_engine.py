import json
import math
import os

# Fixed ACTA case boundaries (metres), per the project architecture
# spec: exactly 3 distinct cases, not profile-dependent.
#   d <= IMMEDIATE_THRESHOLD_M                                -> IMMEDIATE_TRIGGER
#   IMMEDIATE_THRESHOLD_M < d < MULTI_WAYPOINT_THRESHOLD_M     -> WAYPOINT_TRIGGER
#   d >= MULTI_WAYPOINT_THRESHOLD_M                            -> MULTIPLE_WAYPOINTS
IMMEDIATE_THRESHOLD_M = 100
MULTI_WAYPOINT_THRESHOLD_M = 1000


# ---------------------------------------------------------------------------
# New 3-case mathematical spec (MT / TT / beta), exposed via
# ActaEngine.evaluate_telemetry() / build_telemetry_plan_from_signals()
# and the /acta/telemetry-plan + /generate-plan-telemetry endpoints.
#
#   L  < MT                 -> MERGE_TRIGGER
#   MT <= L <= TT            -> IMMEDIATE_TRIGGER
#   L  > TT                 -> WAYPOINT_TRIGGER
#
# Defaults per the spec examples; overridable per-call.
# ---------------------------------------------------------------------------
DEFAULT_MT_M = 150.0     # Merge Threshold
DEFAULT_TT_M = 1000.0    # Trigger Threshold
DEFAULT_BETA_M = 200.0   # Queue Clearance Buffer
DEFAULT_T_TARGET_S = 60.0  # default corridor traversal time target (s), overridable


class ActaEngine:
    def __init__(self):
        # Absolute path to road_profiles.json
        config_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "config",
                "road_profiles.json"
            )
        )

        print(f"Loading profiles from: {config_path}")

        try:
            # utf-8-sig automatically removes the BOM if present
            with open(config_path, "r", encoding="utf-8-sig") as file:
                self.profiles = json.load(file)

            print("Profiles loaded successfully.")
            print(self.profiles)

        except FileNotFoundError:
            raise FileNotFoundError(
                f"road_profiles.json not found at:\n{config_path}"
            )

        except json.JSONDecodeError as e:
            raise ValueError(
                f"Invalid JSON in road_profiles.json:\n{e}"
            )

    def get_profile(self, profile_name: str) -> dict:
        profile = self.profiles.get(profile_name)
        if profile is None:
            raise ValueError(f"Profile '{profile_name}' not found.")
        return profile

    def classify_distance(self, profile: dict, distance: float) -> dict:
        """
        Classify a single inter-signal distance into one of the 3 ACTA
        cases, using fixed, profile-independent boundaries:

            IMMEDIATE_TRIGGER   - d <= 100m: trigger the next signal directly
            WAYPOINT_TRIGGER    - 100m < d < 1000m: trigger the next
                                   signal from a single waypoint
            MULTIPLE_WAYPOINTS  - d >= 1000m: trigger the next signal
                                   from a chain of waypoints

        `queueBuffer` (how far before the target signal a waypoint
        sits) and `waypointSpacing` (gap between chained waypoints in
        the MULTIPLE_WAYPOINTS case) still come from the selected road
        profile -- those remain meaningful per road type even though
        the case boundaries themselves are now fixed.
        """
        queue_buffer = profile["queueBuffer"]
        waypoint_spacing = profile.get("waypointSpacing", 600)
        # Guard against a misconfigured (zero or negative) profile value
        # rather than crashing with a ZeroDivisionError below.
        if waypoint_spacing <= 0:
            waypoint_spacing = 600

        if distance <= IMMEDIATE_THRESHOLD_M:
            return {"distance": distance, "decision": "IMMEDIATE"}

        if distance < MULTI_WAYPOINT_THRESHOLD_M:
            waypoint = max(0, distance - queue_buffer)
            return {
                "distance": distance,
                "decision": "WAYPOINT",
                "waypoint": waypoint,
            }

        # MULTIPLE_WAYPOINTS: split the segment into evenly spaced
        # waypoints, each roughly `waypoint_spacing` apart, with the
        # final approach still respecting queue_buffer before the
        # downstream signal.
        usable_distance = max(0.0, distance - queue_buffer)
        num_waypoints = max(2, math.ceil(usable_distance / waypoint_spacing))
        step = usable_distance / num_waypoints
        waypoints = [round(step * (i + 1), 1) for i in range(num_waypoints)]

        return {
            "distance": distance,
            "decision": "MULTIPLE_WAYPOINTS",
            "waypoints": waypoints,
        }

    def evaluate(self, profile_name: str, segments: list) -> list:
        """Backwards-compatible entry point: classify a raw list of
        inter-signal distances (no signal identity), used by the
        existing /generate-plan simulation endpoint."""

        profile = self.get_profile(profile_name)

        return [self.classify_distance(profile, distance) for distance in segments]

    def build_plan_from_signals(
        self,
        profile_name: str,
        ordered_signals: list,
        t_target_s: float = DEFAULT_T_TARGET_S,
        mt_m: float = DEFAULT_MT_M,
        tt_m: float = DEFAULT_TT_M,
    ) -> list:
        """
        Build a full ACTA activation plan from an ORDERED list of
        on-route signals (as produced by
        RouteMatchService.match_route_signals()["on_route_signals"]),
        each with "signal_id" and "position_on_route_meters".

        Returns a list of segment plans:
            {
                "from_signal": <id>,
                "to_signal": <id>,
                "distance_meters": 520.0,
                "case": "WAYPOINT_TRIGGER",
                ...
            }

        Each segment is ALSO annotated with the exact-spec telemetry
        (target_speed, activation_point_m, lead_preemption_window_s,
        and -- strictly only when distance > tt_m -- a `waypoints`
        list) computed by `compute_segment_telemetry()`. This is the
        single source of truth for both the legacy `case` label and
        the telemetry breakdown: frontend components must read both
        from the same segment object instead of re-deriving telemetry
        client-side, which previously let the two disagree (e.g. a
        segment labelled "Multi-waypoint" via the legacy classifier
        while a client-side recompute rendered a "Merge" breakdown
        for it, or a `waypoints_meters` array bleeding onto a segment
        that never actually qualified for it).

        `waypoint_meters` / `waypoints_meters` (legacy, singular vs.
        plural naming) are ONLY attached when the legacy classifier's
        own decision calls for them -- WAYPOINT gets `waypoint_meters`,
        MULTIPLE_WAYPOINTS gets `waypoints_meters`. A segment can never
        carry both, and a segment that resolved to IMMEDIATE carries
        neither, closing off the leakage path where a global waypoint
        array was previously visible on every signal regardless of
        that signal's own segment length.
        """
        profile = self.get_profile(profile_name)

        case_names = {
            "IMMEDIATE": "IMMEDIATE_TRIGGER",
            "WAYPOINT": "WAYPOINT_TRIGGER",
            "MULTIPLE_WAYPOINTS": "MULTIPLE_WAYPOINTS",
        }

        plan = []

        for i in range(len(ordered_signals) - 1):
            current = ordered_signals[i]
            nxt = ordered_signals[i + 1]

            distance = (
                nxt["position_on_route_meters"] - current["position_on_route_meters"]
            )

            classification = self.classify_distance(profile, distance)
            telemetry = self.compute_segment_telemetry(
                distance, t_target_s, mt_m=mt_m, tt_m=tt_m
            )

            segment = {
                "from_signal": current["signal_id"],
                "to_signal": nxt["signal_id"],
                "distance_meters": round(distance, 1),
                "case": case_names[classification["decision"]],
                # Exact-spec telemetry, computed once, here, from this
                # segment's own distance -- nothing downstream should
                # recompute this independently.
                "case_type": telemetry["case_type"],
                "target_speed": telemetry["target_speed"],
                "activation_point_m": telemetry["activation_point_m"],
                "lead_preemption_window_s": telemetry["lead_preemption_window_s"],
                "parameters": telemetry["parameters"],
            }

            # Legacy waypoint fields: strictly gated on this segment's
            # OWN classification, never attached unconditionally.
            if "waypoint" in classification:
                segment["waypoint_meters"] = round(
                    current["position_on_route_meters"] + classification["waypoint"], 1
                )
            if "waypoints" in classification:
                segment["waypoints_meters"] = [
                    round(current["position_on_route_meters"] + w, 1)
                    for w in classification["waypoints"]
                ]

            # Exact-spec waypoints (W1/W2): strictly gated on L > TT,
            # per the sanity rule -- never attached otherwise.
            if distance > tt_m and "waypoints" in telemetry:
                segment["spec_waypoints"] = [
                    {
                        **wp,
                        "position_on_route_meters": round(
                            current["position_on_route_meters"] + wp["position_m"], 2
                        ),
                    }
                    for wp in telemetry["waypoints"]
                ]

            plan.append(segment)

        return plan

    # ------------------------------------------------------------------
    # New 3-case mathematical spec: MERGE_TRIGGER / IMMEDIATE_TRIGGER /
    # WAYPOINT_TRIGGER, with exact target-speed & clearance-window
    # telemetry per segment.
    # ------------------------------------------------------------------
    @staticmethod
    def compute_segment_telemetry(
        distance_m: float,
        t_target_s: float,
        mt_m: float = DEFAULT_MT_M,
        tt_m: float = DEFAULT_TT_M,
        beta_m: float = DEFAULT_BETA_M,
    ) -> dict:
        """
        Compute exact ACTA telemetry for a single inter-signal segment
        of length `distance_m`, per the mathematical specification:

            MT    - Merge Threshold (m)
            TT    - Trigger Threshold (m)
            beta  - Queue Clearance Buffer (m)
            L     - segment distance (m)
            t_target - desired traversal time for this segment (s)

        Case 1: MERGE_TRIGGER          (L < MT)
        Case 2: IMMEDIATE_TRIGGER      (MT <= L <= TT), AP = 0 (no delay)
        Case 3: WAYPOINT_TRIGGER       (L > TT), AP = L - beta,
                                        W1 = L / 2, W2 = AP

        Also computes, regardless of case:
            v_target  (km/h and m/s) = L / t_target
            T_window  (s)            = beta / v_target(m/s)

        Returns a fully-populated telemetry dict; never mutates inputs.
        """
        L = float(distance_m)

        if t_target_s is None or t_target_s <= 0:
            raise ValueError("t_target_s must be a positive number of seconds.")
        t_target = float(t_target_s)

        # --- Target speed -------------------------------------------------
        # v_target = L / t_target is dimensionally correct as-is (metres
        # / seconds = m/s, then x3.6 for km/h) -- there is no unit bug
        # here. The "7 km/h" symptom comes from applying a FIXED 60s
        # traversal-time target uniformly to every segment regardless
        # of its length: for a short ~120m segment that implies the
        # emergency vehicle is meant to take a full minute to cross it,
        # which is an unrealistic input, not a math error. There is
        # also no Google-traffic-density speed penalty applied anywhere
        # in this calculation -- v_target is a pure geometry/time
        # target, independent of live traffic conditions.
        #
        # Guard rail: clamp the traversal-time target so it can never
        # imply a target speed below a sane emergency-corridor minimum
        # (default 20 km/h / ~5.6 m/s), which mainly protects short
        # segments from an oversized fixed t_target_s producing an
        # absurdly low v_target like the 7 km/h case.
        MIN_TARGET_SPEED_MPS = 5.56  # ~20 km/h
        effective_t_target = min(t_target, L / MIN_TARGET_SPEED_MPS) if L > 0 else t_target

        v_target_mps = L / effective_t_target if effective_t_target > 0 else 0.0
        v_target_kmh = v_target_mps * 3.6

        # --- Lead preemption window (clearance window) --------------------
        # T_window = beta / v_target (guard v_target == 0, i.e. L == 0)
        if v_target_mps > 0:
            t_window_s = beta_m / v_target_mps
        else:
            t_window_s = 0.0

        telemetry = {
            "distance_meters": round(L, 2),
            "parameters": {
                "MT": mt_m,
                "TT": tt_m,
                "beta": beta_m,
                "t_target_s": t_target,
                # The traversal time actually used for v_target, after
                # the minimum-speed guard rail above. Equal to
                # t_target_s unless the fixed target would have implied
                # an unrealistically slow speed for this segment length.
                "effective_t_target_s": round(effective_t_target, 3),
            },
            "target_speed": {
                "kmh": round(v_target_kmh, 3),
                "mps": round(v_target_mps, 3),
            },
            "lead_preemption_window_s": round(t_window_s, 3),
        }

        # --- Case selection -------------------------------------------------
        if L < mt_m:
            # Case 1: Merge Trigger
            telemetry.update({
                "case_type": "MERGE_TRIGGER",
                "action_summary": "Simultaneous corridor preemption (L < MT)",
                "corridor_block": True,
                "activation_point_m": 0.0,
            })

        elif mt_m <= L <= tt_m:
            # Case 2: Immediate Trigger. Activation happens instantly as
            # the vehicle crosses the previous intersection (AP = 0m delay).
            telemetry.update({
                "case_type": "IMMEDIATE_TRIGGER",
                "action_summary": "Preempt downstream signal immediately upon upstream exit",
                "activation_point_m": 0.0,
            })

        else:
            # Case 3: Waypoint / Multi-Waypoint Trigger (L > TT)
            activation_point = L - beta_m
            w1 = L / 2.0
            w2 = activation_point

            telemetry.update({
                "case_type": "WAYPOINT_TRIGGER",
                "action_summary": "Multi-waypoint preemption: advisory pre-notify then lock at AP = L - beta",
                "activation_point_m": round(activation_point, 2),
                "waypoints": [
                    {
                        "id": "W1",
                        "label": "Advisory pre-notification (mid-distance)",
                        "position_m": round(w1, 2),
                    },
                    {
                        "id": "W2",
                        "label": "Final preemption lock (AP)",
                        "position_m": round(w2, 2),
                    },
                ],
            })

        return telemetry

    def evaluate_telemetry(
        self,
        segments: list,
        t_target_s: float = DEFAULT_T_TARGET_S,
        mt_m: float = DEFAULT_MT_M,
        tt_m: float = DEFAULT_TT_M,
        beta_m: float = DEFAULT_BETA_M,
    ) -> list:
        """Evaluate a raw list of inter-signal distances against the new
        3-case mathematical spec, returning full per-segment telemetry."""
        return [
            self.compute_segment_telemetry(d, t_target_s, mt_m, tt_m, beta_m)
            for d in segments
        ]

    def build_telemetry_plan_from_signals(
        self,
        ordered_signals: list,
        t_target_s: float = DEFAULT_T_TARGET_S,
        mt_m: float = DEFAULT_MT_M,
        tt_m: float = DEFAULT_TT_M,
        beta_m: float = DEFAULT_BETA_M,
    ) -> list:
        """
        Build a full telemetry-annotated ACTA plan (new 3-case spec) from
        an ORDERED list of on-route signals, each with "signal_id" and
        "position_on_route_meters".

        Case 1 (MERGE_TRIGGER) segments are additionally tagged with the
        two signal ids being grouped into a single logical corridor block.
        """
        plan = []

        for i in range(len(ordered_signals) - 1):
            current = ordered_signals[i]
            nxt = ordered_signals[i + 1]

            distance = (
                nxt["position_on_route_meters"] - current["position_on_route_meters"]
            )

            telemetry = self.compute_segment_telemetry(
                distance, t_target_s, mt_m, tt_m, beta_m
            )

            segment = {
                "from_signal": current["signal_id"],
                "to_signal": nxt["signal_id"],
                **telemetry,
            }

            if telemetry["case_type"] == "MERGE_TRIGGER":
                segment["corridor_block_signals"] = [
                    current["signal_id"], nxt["signal_id"],
                ]

            # Convert waypoint offsets (relative to `current`) into
            # absolute route positions, same convention as the legacy
            # build_plan_from_signals().
            if "waypoints" in segment:
                base = current["position_on_route_meters"]
                for wp in segment["waypoints"]:
                    wp["position_on_route_meters"] = round(base + wp["position_m"], 2)
            if segment.get("case_type") in ("IMMEDIATE_TRIGGER", "WAYPOINT_TRIGGER"):
                base = current["position_on_route_meters"]
                segment["activation_point_on_route_meters"] = round(
                    base + segment["activation_point_m"], 2
                )

            plan.append(segment)

        return plan