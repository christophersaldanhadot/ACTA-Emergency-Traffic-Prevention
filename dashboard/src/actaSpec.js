// NOTE: this file previously also contained computeSegmentTelemetry(),
// a client-side reimplementation of
// backend/services/acta_engine.py::ActaEngine.compute_segment_telemetry(),
// using its own hardcoded default MT/TT/beta/t_target_s. That gave the
// dashboard two independent engines computing telemetry for the same
// segment -- the legacy /generate-full-plan classifier (case,
// waypoint_meters/waypoints_meters) and this client-side recompute
// (case_type, target_speed, W1/W2) -- which could, and did, disagree
// for the same signal (e.g. "Multi-waypoint" from one engine, "Merge"
// from the other for the very same segment, plus a mismatched
// waypoint array bleeding across signals). The backend is now the
// single source of truth: ActaEngine.build_plan_from_signals() attaches
// case_type / target_speed / activation_point_m / spec_waypoints
// directly onto each acta_plan segment, computed once, server-side.
// The inspection card reads only that. Do not reintroduce a
// client-side recompute of this telemetry.

/**
 * Preemption status for a signal, derived from parsed TraCI simulation
 * events (sim.events, as logged by PreemptionController). Reflects the
 * controller's real event vocabulary: ADVISORY, PREEMPT_ON, PREEMPT_OFF.
 *
 *   "Idle"                 - no simulation run yet, or no events for this signal
 *   "Advisory Sent"        - an ADVISORY (queue pre-notification) fired, no lock yet
 *   "Active (Green Hold)"  - PREEMPT_ON fired and hasn't been released (PREEMPT_OFF)
 */
export function derivePreemptionStatus(events, signalId) {
  if (!events || events.length === 0) return { status: "Idle", lastEvent: null };

  const idStr = String(signalId);
  const relevant = events.filter((e) => {
    if (e.signal_id !== undefined) return String(e.signal_id) === idStr;
    if (e.signal_ids) return e.signal_ids.map(String).includes(idStr);
    return false;
  });

  if (relevant.length === 0) return { status: "Idle", lastEvent: null };

  // Walk chronologically; PREEMPT_ON without a later PREEMPT_OFF means
  // the hold is still active as of the latest logged state.
  let status = "Idle";
  let lastEvent = null;
  for (const e of relevant) {
    if (e.event === "ADVISORY") {
      status = "Advisory Sent";
    } else if (e.event === "PREEMPT_ON") {
      status = "Active (Green Hold)";
    } else if (e.event === "PREEMPT_OFF") {
      status = "Idle";
    }
    lastEvent = e;
  }
  return { status, lastEvent };
}
