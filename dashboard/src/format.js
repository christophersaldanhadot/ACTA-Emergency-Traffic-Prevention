export function fmtKm(meters) {
  if (meters == null) return "-";
  return meters >= 1000 ? `${(meters / 1000).toFixed(2)} km` : `${meters.toFixed(0)} m`;
}

export function fmtSeconds(s) {
  if (s == null) return "-";
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return m > 0 ? `${m}m ${r}s` : `${r}s`;
}

// Coordinates are truncated to 4 decimal places (~11m precision) for
// display -- plenty for a signal-location reference, and it keeps the
// column narrow and legible instead of dragging the table wide with
// full float precision. Full precision is still available on hover
// via the `title` tooltip (see fmtCoordFull).
export function fmtCoord(lat, lng) {
  if (lat == null || lng == null) return "-";
  return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
}

export function fmtCoordFull(lat, lng) {
  if (lat == null || lng == null) return "-";
  return `${lat}, ${lng}`;
}

// Signed delta formatting for the schematic corridor timeline, e.g.
// "+141m" / "+1.2km" -- the badge between two nodes that stands in
// for the real-world gap once nodes themselves are laid out on an
// equal-spacing (not to-scale) axis.
export function fmtDelta(meters) {
  if (meters == null) return "-";
  return meters >= 1000 ? `+${(meters / 1000).toFixed(1)}km` : `+${meters.toFixed(0)}m`;
}

export const CASE_LABELS = {
  IMMEDIATE_TRIGGER: "Immediate",
  WAYPOINT_TRIGGER: "Waypoint",
  MULTIPLE_WAYPOINTS: "Multi-waypoint",
  MERGE_TRIGGER: "Merge",
};

export function caseColor(caseName) {
  switch (caseName) {
    case "IMMEDIATE_TRIGGER": return "#22d3a8";
    case "WAYPOINT_TRIGGER": return "#ffb454";
    case "MULTIPLE_WAYPOINTS": return "#c084fc";
    case "MERGE_TRIGGER": return "#4ea1ff";
    default: return "#8a8da3";
  }
}

// e.g. "48 km/h / 13.3 m/s"
export function fmtSpeed(kmh, mps) {
  if (kmh == null || mps == null) return "-";
  return `${kmh.toFixed(0)} km/h / ${mps.toFixed(1)} m/s`;
}

const REASON_LABELS = {
  off_route: "Off route",
  opposite_direction: "Opposite direction",
  direction_unknown: "Direction unknown",
  ambiguous_direction: "Ambiguous direction",
};

export function fmtReason(reason) {
  if (!reason) return "-";
  return REASON_LABELS[reason] || reason.replaceAll("_", " ");
}
