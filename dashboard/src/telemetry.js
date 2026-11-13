function fmtKmShort(meters) {
  if (meters == null) return "-";
  return meters >= 1000 ? `${(meters / 1000).toFixed(2)}km` : `${meters.toFixed(0)}m`;
}

function fmtSecShort(s) {
  if (s == null) return "-";
  return `${Math.round(s)}s`;
}

/**
 * Builds an ordered array of {level, message} telemetry entries from
 * the current plan / simulation / error state. Pure function, no
 * side effects -- App.jsx just re-derives this on every render.
 */
export function buildTelemetry({ plan, planError, sim, simError }) {
  const entries = [];

  if (planError) {
    entries.push({ level: "ERROR", message: `Route/plan generation failed: ${planError}` });
    return entries;
  }

  if (!plan) {
    entries.push({ level: "INFO", message: "Idle. Submit a route to begin." });
    return entries;
  }

  entries.push({
    level: "INFO",
    message: `Route computed: "${plan.origin}" -> "${plan.destination}" (${fmtKmShort(plan.route_distance_meters)}, ${fmtSecShort(plan.route_duration_seconds)}, profile=${plan.profile || "?"})`,
  });

  entries.push({
    level: "INFO",
    message: `Overpass: ${plan.number_of_candidate_signals} candidate traffic-signal nodes fetched for bounding box`,
  });

  entries.push({
    level: "OK",
    message: `Route matching: ${plan.signals.length} signal(s) confirmed on-route (A->B direction)`,
  });

  const discardReasons = {};
  for (const s of plan.discarded_signals || []) {
    const reason = s.reason || "unknown";
    discardReasons[reason] = (discardReasons[reason] || 0) + 1;
  }
  for (const [reason, count] of Object.entries(discardReasons)) {
    entries.push({
      level: "WARN",
      message: `Route matching: ${count} signal(s) rejected -- reason: ${reason}`,
    });
  }

  const caseCounts = {};
  for (const seg of plan.acta_plan || []) {
    caseCounts[seg.case_type] = (caseCounts[seg.case_type] || 0) + 1;
  }
  if (Object.keys(caseCounts).length > 0) {
    const summary = Object.entries(caseCounts).map(([c, n]) => `${c}=${n}`).join(", ");
    entries.push({ level: "INFO", message: `ACTA classification: ${summary}` });
  }

  if (simError) {
    entries.push({ level: "ERROR", message: `SUMO simulation failed: ${simError}` });
  }

  if (sim) {
    entries.push({ level: "INFO", message: "SUMO scenario compiled (netconvert) and TraCI session started" });

    for (const e of sim.events) {
      const time = `t=${Math.round(e.sim_time)}s`;
      if (e.event === "PREEMPT_ON") {
        entries.push({
          level: "INFO",
          message: `[${time}] PREEMPT_ON  tls=${e.tls_id} signal=${e.signal_id ?? "?"} case=${e.case}`,
        });
      } else if (e.event === "PREEMPT_OFF") {
        entries.push({
          level: "INFO",
          message: `[${time}] PREEMPT_OFF tls=${e.tls_id} -- normal operation resumed`,
        });
      } else if (e.event === "ADVISORY") {
        entries.push({
          level: "INFO",
          message: `[${time}] ADVISORY -- pre-notification for signal ${e.signal_ids?.join(",")} (${e.case})`,
        });
      }
    }

    entries.push({
      level: "OK",
      message: `Simulation complete: arrival ${fmtSecShort(sim.with_preemption_arrival_s)}${
        sim.time_saved_s !== undefined ? `, time saved vs. baseline: ${sim.time_saved_s}s` : ""
      }`,
    });
  }

  return entries;
}
