import { useMemo, memo, useState, useRef, useEffect, useCallback } from "react";
import { CASE_LABELS, caseColor, fmtDelta } from "./format";

const HEIGHT = 210;
const MARGIN = 56;
const TRACK_Y = 78;
const MIN_WIDTH = 1100;
const PX_PER_SIGNAL = 130; // schematic spacing is fixed-pitch, not distance-based
const MAX_WIDTH = 6500;
const LABEL_ROTATION = 40; // degrees

// Per-segment travel time (ms) for the run animation, keyed by ACTA
// case_type -- the exact same field the sidebar uses as its single
// source of truth (segment.case_type), not the legacy `case` field.
// IMMEDIATE_TRIGGER / WAYPOINT_TRIGGER simulate a cleared "green wave" --
// the vehicle glides through quickly. MULTIPLE_WAYPOINTS stands in for a
// complex merge zone (several waypoints to resolve = more conflict
// geometry), and an un-preempted baseline hop (no ACTA segment at all,
// i.e. before DISPATCH or a rejected gap) gets the longest pause to
// visually read as "still fighting normal signal timing."
const SEGMENT_DURATION_MS = {
  MERGE_TRIGGER: 450,
  IMMEDIATE_TRIGGER: 550,
  WAYPOINT_TRIGGER: 700,
  MULTIPLE_WAYPOINTS: 1500,
  BASELINE: 1900,
};

function segmentDuration(caseName) {
  return SEGMENT_DURATION_MS[caseName] ?? SEGMENT_DURATION_MS.BASELINE;
}

function easeInOutQuad(t) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

// This is a SCHEMATIC (equal-spacing) layout, not a to-scale map: each
// signal gets an identical pitch along the axis regardless of its real
// distance from its neighbours. A 15km corridor with signals bunched
// into a 200m cluster is unreadable on a physical scale (nodes stack on
// top of each other); schematic spacing is how real transit/metro maps
// solve this. The true distance is never lost -- it's surfaced as the
// "+141m" / "+1.2km" badge on the segment between each pair of nodes.
function CorridorView({ plan, simEvents, selectedId, onSelect, onHover, etaSeconds }) {
  const signals = plan?.signals;
  const segments = plan?.acta_plan || [];

  const width = useMemo(
    () => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, (signals?.length || 0) * PX_PER_SIGNAL)),
    [signals]
  );
  const usableWidth = width - MARGIN * 2;
  const n = signals?.length || 0;

  // ---- Run Visualization: animated ambulance sweep ----
  const [running, setRunning] = useState(false);
  const [vehicleX, setVehicleX] = useState(null); // null = not running
  const [clearedIds, setClearedIds] = useState(() => new Set());
  const [pingId, setPingId] = useState(null); // brief per-node ping pulse
  const [elapsedMs, setElapsedMs] = useState(0);
  const rafRef = useRef(null);
  const startRef = useRef(0);

  const stopAnimation = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }, []);

  useEffect(() => () => stopAnimation(), [stopAnimation]);

  // Equal-pitch x position by index, not by physical position_on_route_meters.
  const xByIndex = (i) => (n <= 1 ? MARGIN + usableWidth / 2 : MARGIN + (i / (n - 1)) * usableWidth);

  const signalIndexById = useMemo(() => {
    const map = new Map();
    (signals || []).forEach((s, i) => map.set(s.signal_id, i));
    return map;
  }, [signals]);

  // O(1) segment lookup by "from->to" pair instead of Array.find() per
  // rendered gap, same reasoning as the signal-id map above.
  const segmentByPair = useMemo(() => {
    const map = new Map();
    for (const seg of segments) map.set(`${seg.from_signal}->${seg.to_signal}`, seg);
    return map;
  }, [segments]);

  // Match by signal_id, not by position: an event's "fire" position
  // (trigger_position_m) is often NOT the same point as the junction it
  // preempts (e.g. DISPATCH fires at position 0 but preempts the first
  // signal, which may be hundreds of metres down the route). The event
  // log carries signal_id explicitly for exactly this reason.
  const preemptedSignalIds = useMemo(() => {
    return new Set(
      (simEvents || [])
        .filter((e) => e.event === "PREEMPT_ON" && e.signal_id !== undefined)
        .map((e) => e.signal_id)
    );
  }, [simEvents]);

  const totalRunMs = useMemo(() => {
    if (n < 2) return 0;
    let total = 0;
    for (let i = 0; i < n - 1; i++) {
      const seg = segmentByPair.get(`${signals[i].signal_id}->${signals[i + 1].signal_id}`);
      total += segmentDuration(seg?.case_type);
    }
    return total;
  }, [n, signals, segmentByPair]);

  const runVisualization = useCallback(() => {
    if (!signals || n < 2 || running) return;
    stopAnimation();
    setClearedIds(new Set());
    setPingId(null);
    setRunning(true);
    setVehicleX(xByIndex(0));
    setElapsedMs(0);
    startRef.current = performance.now();

    const legDurations = [];
    for (let i = 0; i < n - 1; i++) {
      const seg = segmentByPair.get(`${signals[i].signal_id}->${signals[i + 1].signal_id}`);
      legDurations.push(segmentDuration(seg?.case_type));
    }
    const total = legDurations.reduce((a, b) => a + b, 0);

    function tick(now) {
      const t = now - startRef.current;
      setElapsedMs(Math.min(t, total));

      // Find which leg we're in and interpolate within it (eased, so
      // it visibly decelerates into slower/complex legs rather than
      // snapping speed at each node boundary).
      let acc = 0;
      let legIndex = legDurations.length - 1;
      let legT = 1;
      for (let i = 0; i < legDurations.length; i++) {
        if (t <= acc + legDurations[i]) {
          legIndex = i;
          legT = legDurations[i] > 0 ? (t - acc) / legDurations[i] : 1;
          break;
        }
        acc += legDurations[i];
      }
      legT = Math.max(0, Math.min(1, legT));
      const eased = easeInOutQuad(legT);
      const x1 = xByIndex(legIndex);
      const x2 = xByIndex(legIndex + 1);
      setVehicleX(x1 + (x2 - x1) * eased);

      // Ping + mark-cleared the node we just reached as each leg completes.
      if (legT >= 0.999) {
        const arrivedSignal = signals[legIndex + 1];
        setClearedIds((prev) => {
          if (prev.has(arrivedSignal.signal_id)) return prev;
          const next = new Set(prev);
          next.add(arrivedSignal.signal_id);
          return next;
        });
        setPingId(arrivedSignal.signal_id);
      }

      if (t < total) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setElapsedMs(total);
        setRunning(false);
        setTimeout(() => setPingId(null), 700);
      }
    }
    rafRef.current = requestAnimationFrame(tick);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signals, n, running, stopAnimation, segmentByPair]);

  if (!plan || !signals || signals.length === 0) {
    return <div className="empty-state">No on-route signals to display.</div>;
  }

  return (
    <div className="corridor">
      <div className="corridor-legend">
        {Object.entries(CASE_LABELS).map(([key, label]) => (
          <span key={key} className="legend-item">
            <span className="legend-swatch" style={{ background: caseColor(key) }} />
            {label}
          </span>
        ))}
        <span className="legend-item">
          <span className="legend-swatch legend-swatch--ring" />
          Preempted
        </span>
        <span className="legend-item legend-hint">Schematic layout, not to scale &middot; click or hover a node to inspect &rarr;</span>
        <button
          type="button"
          className="btn-secondary btn-sm run-viz-btn"
          onClick={runVisualization}
          disabled={running || n < 2}
        >
          {running ? "Clearing corridor..." : "▶ Run Visualization"}
        </button>
      </div>

      <div className="corridor-scroll" style={{ position: "relative", width, overflow: "visible" }}>
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width={width} height={HEIGHT}>
        <defs>
          <linearGradient id="corridorGradient" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#262838" />
            <stop offset="100%" stopColor="#35c1ff" stopOpacity="0.55" />
          </linearGradient>
        </defs>

        {/* base line: subtle gradient conveys start -> end progression */}
        <line x1={xByIndex(0)} y1={TRACK_Y} x2={xByIndex(n - 1)} y2={TRACK_Y} stroke="url(#corridorGradient)" strokeWidth="3" />

        {/* origin / destination labels */}
        <text x={xByIndex(0)} y={20} fontSize="10" fill="#8a8da3">A (origin)</text>
        <text x={xByIndex(n - 1)} y={20} fontSize="10" fill="#8a8da3" textAnchor="end">B (destination)</text>

        {/* segments between consecutive on-route signals: colored by case,
            with a badge showing the real-world distance + case, and small
            directional arrows to reinforce flow toward the destination */}
        {signals.slice(0, -1).map((fromSig, i) => {
          const toSig = signals[i + 1];
          const x1 = xByIndex(i);
          const x2 = xByIndex(i + 1);
          const mid = (x1 + x2) / 2;
          // The segment's case comes from acta_plan (keyed by to_signal),
          // not assumed to be i -> i+1 in acta_plan's own array order.
          // `case_type` is the exact-spec field -- the SAME single
          // source of truth the sidebar (InspectionDrawer) reads for
          // its "Assigned ACTA case" / breakdown. The legacy `case`
          // field (from the older 100m/1000m classifier) is a
          // different, disagreeing classification and must not drive
          // any visual here -- that mismatch (e.g. legacy WAYPOINT vs.
          // exact-spec IMMEDIATE_TRIGGER for the same 690m segment) is
          // exactly what produced the wrong yellow waypoint badge.
          const seg = segmentByPair.get(`${fromSig.signal_id}->${toSig.signal_id}`);
          const color = seg ? caseColor(seg.case_type) : "#33364a";
          const deltaM = toSig.position_on_route_meters - fromSig.position_on_route_meters;
          const isWaypointCase =
            seg?.case_type === "WAYPOINT_TRIGGER" || seg?.case_type === "MULTIPLE_WAYPOINTS";

          return (
            <g key={`seg-${fromSig.signal_id}`}>
              <line x1={x1} y1={TRACK_Y} x2={x2} y2={TRACK_Y} stroke={color} strokeWidth="4" />
              {/* direction arrows */}
              {[0.32, 0.68].map((f) => {
                const ax = x1 + (x2 - x1) * f;
                return (
                  <polygon
                    key={f}
                    points={`${ax - 4},${TRACK_Y - 4} ${ax + 5},${TRACK_Y} ${ax - 4},${TRACK_Y + 4}`}
                    fill={color}
                    opacity="0.85"
                  />
                );
              })}
              {/* waypoint sub-markers -- ONLY rendered when this
                  segment's own case_type is strictly WAYPOINT_TRIGGER
                  or MULTIPLE_WAYPOINTS (the same gate the sidebar
                  uses). IMMEDIATE_TRIGGER and MERGE_TRIGGER segments
                  never reach here, so they draw a clean line to the
                  next node with no intermediate badges. Positions
                  come from spec_waypoints (W1/W2, exact-spec), the
                  same field the sidebar's WaypointCaseDetail reads --
                  not the legacy waypoint_meters/waypoints_meters
                  fields, which belong to a different classifier and
                  can disagree with case_type. */}
              {isWaypointCase &&
                seg?.spec_waypoints?.map((wp) => (
                  <circle
                    key={wp.id}
                    cx={
                      x1 +
                      (wp.position_on_route_meters - fromSig.position_on_route_meters) /
                        (toSig.position_on_route_meters - fromSig.position_on_route_meters) *
                        (x2 - x1)
                    }
                    cy={TRACK_Y}
                    r="3"
                    fill={color}
                  />
                ))}
              {/* distance + case badge */}
              <g transform={`translate(${mid}, ${TRACK_Y - 26})`}>
                <rect x={-30} y={-11} width={60} height={22} rx={5} fill="#181a24" stroke={color} strokeWidth="1" />
                <text x={0} y={-1} fontSize="8" fontWeight="700" fill={color} textAnchor="middle">
                  {seg ? (CASE_LABELS[seg.case_type] || seg.case_type) : ""}
                </text>
                <text x={0} y={8} fontSize="8" fill="#8a8da3" textAnchor="middle" fontFamily="monospace">
                  {fmtDelta(deltaM)}
                </text>
              </g>
            </g>
          );
        })}

        {/* signal markers with rotated labels -- diagonal placement means
            adjacent labels extend into free vertical space instead of
            colliding horizontally, so it scales to dense corridors without
            needing per-pair overlap detection. */}
        {signals.map((s) => {
          const i = signalIndexById.get(s.signal_id);
          const cx = xByIndex(i);
          const preempted = preemptedSignalIds.has(s.signal_id);
          const selected = selectedId != null && String(selectedId) === String(s.signal_id);
          const runCleared = clearedIds.has(s.signal_id);
          const isPinging = pingId === s.signal_id;
          return (
            <g
              key={s.signal_id}
              className="corridor-node"
              onClick={() => onSelect?.(s.signal_id)}
              onMouseEnter={() => onHover?.(s.signal_id)}
              onMouseLeave={() => onHover?.(null)}
              style={{ cursor: onSelect ? "pointer" : "default" }}
            >
              {selected && (
                <circle cx={cx} cy={TRACK_Y} r="13" fill="none" stroke="#7c5cff" strokeWidth="1.5" opacity="0.6" />
              )}
              {isPinging && (
                <circle cx={cx} cy={TRACK_Y} r="8" fill="none" stroke="#22d3a8" strokeWidth="2">
                  <animate attributeName="r" from="8" to="22" dur="0.7s" begin="0s" fill="freeze" />
                  <animate attributeName="opacity" from="0.8" to="0" dur="0.7s" begin="0s" fill="freeze" />
                </circle>
              )}
              <circle
                cx={cx}
                cy={TRACK_Y}
                r={preempted ? 8 : selected ? 7 : 6}
                fill={selected ? "#7c5cff" : runCleared ? "#132a26" : "#08090d"}
                stroke={runCleared ? "#22d3a8" : preempted ? "#7c5cff" : "#eef0f7"}
                strokeWidth={runCleared ? 3 : 2}
                style={runCleared ? { filter: "drop-shadow(0 0 5px #22d3a8)" } : undefined}
              />
              <text
                x={cx + 7}
                y={TRACK_Y + 16}
                fontSize="10"
                fill={selected ? "#7c5cff" : "#f4f2ff"}
                fontWeight={selected ? "700" : "500"}
                textAnchor="start"
                fontFamily="monospace"
                style={{ paintOrder: "stroke", stroke: "#08090d", strokeWidth: 3 }}
                transform={`rotate(${LABEL_ROTATION} ${cx + 7} ${TRACK_Y + 16})`}
              >
                {s.signal_id}
              </text>
            </g>
          );
        })}
      </svg>

      {vehicleX != null && (
        <div
          className="ambulance-overlay"
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            width: "100%",
            height: HEIGHT,
            pointerEvents: "none",
          }}
        >
          <div
            className="ambulance-timer"
            style={{
              position: "absolute",
              left: vehicleX,
              top: TRACK_Y - 46,
              transform: "translateX(-50%)",
            }}
          >
            {fmtRunClock(elapsedMs, running, totalRunMs, etaSeconds)}
          </div>
          <div
            className="ambulance-icon"
            style={{
              position: "absolute",
              left: vehicleX,
              top: TRACK_Y - 14,
              transform: "translateX(-50%)",
            }}
          >
            🚑
          </div>
        </div>
      )}
      </div>
    </div>
  );
}

// The floating timer ticks up in simulated seconds while the run is in
// progress (mapped from the animation's own elapsed ms so it has a
// human "seconds elapsed" feel rather than showing raw ms), and locks
// to the plan's actual calculated ACTA ETA the instant the ambulance
// reaches the destination -- so the number the audience sees land on
// is always the real backend figure, not an animation artifact.
function fmtRunClock(elapsedMs, running, totalRunMs, etaSeconds) {
  if (!running && etaSeconds != null && totalRunMs > 0) {
    const m = Math.floor(etaSeconds / 60);
    const s = Math.round(etaSeconds % 60);
    return (
      <span className="ambulance-timer-badge ambulance-timer-final">
        {m > 0 ? `${m}m ${s}s` : `${s}s`} ETA
      </span>
    );
  }
  const simulatedElapsedS =
    totalRunMs > 0 && etaSeconds != null ? (elapsedMs / totalRunMs) * etaSeconds : elapsedMs / 1000;
  const m = Math.floor(simulatedElapsedS / 60);
  const s = Math.round(simulatedElapsedS % 60);
  return (
    <span className="ambulance-timer-badge">
      {m > 0 ? `${m}m ${s}s` : `${s}s`}
    </span>
  );
}

// Props are referentially stable across unrelated App state changes
// (plan/simEvents only change when a new plan/simulation actually
// completes), so memoizing skips the SVG-generation work entirely on
// re-renders triggered by sibling state (e.g. toggling the compare
// checkbox, opening the telemetry drawer). selectedId now also flows
// from hover, which changes more often -- still cheap since it's a
// primitive prop and this is a shallow memo comparison.
export default memo(CorridorView);
