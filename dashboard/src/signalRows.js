// Merges plan.signals (on-route) and plan.discarded_signals (off-route)
// into one flat, position-ordered list so the UI has a single source
// of truth for "all signals" instead of two disconnected tables.
//
// Each on-route signal is additionally annotated with the ACTA
// segment that triggers *it* (the segment whose to_signal matches),
// so the case/trigger-type for a signal can be shown right on its
// row instead of requiring a second lookup into acta_plan.
export function buildSignalRows(plan) {
  if (!plan) return [];

  const segmentByTarget = new Map();
  for (const seg of plan.acta_plan || []) {
    segmentByTarget.set(seg.to_signal, seg);
  }

  const onRoute = (plan.signals || []).map((s) => ({
    key: `on-${s.signal_id}`,
    status: "ON_ROUTE",
    signal_id: s.signal_id,
    direction: s.direction,
    latitude: s.latitude,
    longitude: s.longitude,
    position_on_route_meters: s.position_on_route_meters,
    distance_from_route_meters: s.distance_from_route_meters,
    match_confidence: s.match_confidence,
    segment: segmentByTarget.get(s.signal_id) || null,
    reason: null,
  }));

  const offRoute = (plan.discarded_signals || []).map((s) => ({
    key: `off-${s.signal_id}`,
    status: "OFF_ROUTE",
    signal_id: s.signal_id,
    direction: s.direction,
    latitude: s.latitude,
    longitude: s.longitude,
    position_on_route_meters: s.position_on_route_meters,
    distance_from_route_meters: s.distance_from_route_meters,
    match_confidence: s.match_confidence,
    segment: null,
    reason: s.reason,
  }));

  return [...onRoute, ...offRoute].sort((a, b) => {
    const pa = a.position_on_route_meters ?? Infinity;
    const pb = b.position_on_route_meters ?? Infinity;
    return pa - pb;
  });
}
