import { fmtKm, fmtCoordFull, fmtReason, fmtSpeed, CASE_LABELS } from "./format";
import { derivePreemptionStatus } from "./actaSpec";

const STATUS_CLASS = {
  Idle: "status-idle",
  "Advisory Sent": "status-advisory",
  "Active (Green Hold)": "status-active",
};

// Single source of truth: status is always derived from sim events for
// this signal. `derivePreemptionStatus` already returns "Idle" when
// `sim` is null/has no events for this signal, so there is no separate
// "no sim yet" placeholder state to keep in sync by hand.
function PreemptionStatusBadge({ status }) {
  return (
    <span className={`status-pill ${STATUS_CLASS[status] || ""}`}>
      <span className="status-dot" aria-hidden="true" />
      {status}
    </span>
  );
}

// Case 1: MERGE_TRIGGER (L < MT)
function MergeCaseDetail({ segment }) {
  return (
    <>
      <div className="detail-item">
        <span className="detail-label">Segment length (L)</span>
        <span className="detail-value">
          {fmtKm(segment.distance_meters)} <span className="detail-value--muted">(&lt; MT)</span>
        </span>
      </div>
      <div className="detail-item">
        <span className="detail-label">Junction mode</span>
        <span className="detail-value">Merged Logical Block</span>
      </div>
      <div className="detail-item">
        <span className="detail-label">Action</span>
        <span className="detail-value">Dual-intersection simultaneous signal lock</span>
      </div>
    </>
  );
}

// Case 2: IMMEDIATE_TRIGGER (MT <= L <= TT)
function ImmediateCaseDetail({ segment }) {
  return (
    <>
      <div className="detail-item">
        <span className="detail-label">Segment length (L)</span>
        <span className="detail-value">
          {fmtKm(segment.distance_meters)} <span className="detail-value--muted">(MT &le; L &le; TT)</span>
        </span>
      </div>
      <div className="detail-item">
        <span className="detail-label">Trigger offset</span>
        <span className="detail-value">
          0 m <span className="detail-value--muted">(Fired at upstream exit)</span>
        </span>
      </div>
      <div className="detail-item">
        <span className="detail-label">Action</span>
        <span className="detail-value">Immediate downstream green phase request</span>
      </div>
    </>
  );
}

// Case 3: WAYPOINT_TRIGGER / MULTIPLE_WAYPOINTS (L > TT).
// Waypoints are ONLY ever read here, and only reached via the strict
// switch in CaseBreakdown below -- never rendered for MERGE_TRIGGER or
// IMMEDIATE_TRIGGER segments, and never rendered from a different
// segment than the one whose case_type gated this branch.
function WaypointCaseDetail({ segment }) {
  const beta = segment.parameters?.beta;
  const ap = segment.activation_point_m;
  const w1 = segment.spec_waypoints?.find((w) => w.id === "W1");
  const w2 = segment.spec_waypoints?.find((w) => w.id === "W2");

  return (
    <>
      <div className="detail-item">
        <span className="detail-label">Segment length (L)</span>
        <span className="detail-value">
          {fmtKm(segment.distance_meters)} <span className="detail-value--muted">(&gt; TT)</span>
        </span>
      </div>
      {beta != null && (
        <div className="detail-item">
          <span className="detail-label">Queue buffer (&beta;)</span>
          <span className="detail-value">{fmtKm(beta)}</span>
        </div>
      )}
      {ap != null && (
        <div className="detail-item">
          <span className="detail-label">Activation point (AP = L &minus; &beta;)</span>
          <span className="detail-value">{fmtKm(ap)} from upstream junction</span>
        </div>
      )}

      {(w1 || w2) && (
        <div className="waypoint-timeline">
          {w1 && (
            <div className="timeline-step">
              <span className="timeline-dot timeline-dot--advisory" aria-hidden="true" />
              <div className="timeline-step-body">
                <div className="timeline-step-title">W1 Advisory &mdash; Queue Flush</div>
                <div className="timeline-step-sub">at {fmtKm(w1.position_m)}</div>
              </div>
            </div>
          )}
          {w2 && (
            <div className="timeline-step">
              <span className="timeline-dot timeline-dot--lock" aria-hidden="true" />
              <div className="timeline-step-body">
                <div className="timeline-step-title">W2 Final Lock &mdash; Green Signal Hold</div>
                <div className="timeline-step-sub">AP = {fmtKm(ap)}</div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Legacy chained-waypoint list (MULTIPLE_WAYPOINTS only). Kept
          separate from spec_waypoints (W1/W2) since they come from a
          different classifier; still strictly gated by the same
          case_type switch, so it can never render for a segment whose
          own case_type isn't a waypoint case. */}
      {segment.waypoints_meters && segment.waypoints_meters.length > 0 && (
        <div className="detail-item">
          <span className="detail-label">Waypoints (chained)</span>
          <span className="detail-value">
            {segment.waypoints_meters.map((w) => fmtKm(w)).join(", ")}
          </span>
        </div>
      )}

      {segment.lead_preemption_window_s != null && (
        <div className="detail-item">
          <span className="detail-label">Lead window</span>
          <span className="detail-value">
            {segment.lead_preemption_window_s.toFixed(1)} s before arrival
          </span>
        </div>
      )}
    </>
  );
}

// Strict single-source-of-truth dispatch: the ENTIRE breakdown display
// (header tag, body, and whether waypoints are shown at all) comes from
// one field -- segment.case_type -- on one segment object. There is no
// branch here that mixes fields from a different segment, and no
// fallthrough that lets a waypoint list render outside its own case.
function CaseBreakdown({ segment }) {
  if (!segment) {
    return (
      <span className="detail-value detail-value--muted">
        No downstream segment from this signal &mdash; nothing to trigger.
      </span>
    );
  }
  switch (segment.case_type) {
    case "MERGE_TRIGGER":
      return <MergeCaseDetail segment={segment} />;
    case "IMMEDIATE_TRIGGER":
      return <ImmediateCaseDetail segment={segment} />;
    case "WAYPOINT_TRIGGER":
    case "MULTIPLE_WAYPOINTS":
      return <WaypointCaseDetail segment={segment} />;
    default:
      return <span className="detail-value detail-value--muted">Unrecognized case type.</span>;
  }
}

export default function InspectionDrawer({ plan, sim, selectedId, onClose }) {
  if (selectedId == null) {
    return (
      <div className="inspection-drawer inspection-drawer--empty">
        <div className="empty-state">Click or hover a signal node to inspect it.</div>
      </div>
    );
  }

  const onRoute = plan.signals.find((s) => String(s.signal_id) === String(selectedId));
  const discarded = plan.discarded_signals.find((s) => String(s.signal_id) === String(selectedId));
  const signal = onRoute || discarded;

  if (!signal) {
    return (
      <div className="inspection-drawer inspection-drawer--empty">
        <div className="empty-state">Signal not found in this plan.</div>
      </div>
    );
  }

  // The outgoing segment (from this signal to the NEXT one downstream)
  // is this node's own preemption behavior, and is the single object
  // used for every part of the card that describes "this signal's
  // case": the header tag, the case breakdown, the waypoints, and the
  // target speed. The incoming segment (from the PREVIOUS signal into
  // this one) is a different segment with its own, unrelated
  // case_type/distance/waypoints and is only ever used for the
  // separate "distance FROM previous signal" context line -- it must
  // never feed the same case_type switch or waypoint list as the
  // outgoing segment, which is what previously let a 123m outgoing
  // segment's card show a waypoint array that actually belonged to a
  // much longer incoming segment.
  const outgoingSegment = (plan.acta_plan || []).find((seg) => seg.from_signal === signal.signal_id);
  const incomingSegment = (plan.acta_plan || []).find((seg) => seg.to_signal === signal.signal_id);

  const { status: preemptionStatus } = derivePreemptionStatus(sim?.events, signal.signal_id);

  return (
    <div className="inspection-drawer">
      <div className="inspection-header">
        <div>
          <div className="inspection-title">Signal {signal.signal_id}</div>
          <span className={`tag ${onRoute ? "on-route" : "off-route"}`}>
            {onRoute ? "On route" : "Off route"}
          </span>
        </div>
        <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">&times;</button>
      </div>

      {/* 1. Header & dynamic status */}
      <div className="inspection-body">
        <div className="detail-item">
          <span className="detail-label">Signal ID</span>
          <span className="detail-value">{signal.signal_id}</span>
        </div>
        <div className="detail-item">
          <span className="detail-label">Node coordinates</span>
          <span className="detail-value coord">{fmtCoordFull(signal.latitude, signal.longitude)}</span>
        </div>
        {signal.match_confidence != null && (
          <div className="detail-item">
            <span className="detail-label">Match confidence</span>
            <span className="detail-value">{(signal.match_confidence * 100).toFixed(0)}%</span>
          </div>
        )}
        <div className="detail-item">
          <span className="detail-label">Direction</span>
          <span className="detail-value">{signal.direction?.replaceAll("_", " ") || "-"}</span>
        </div>
        {onRoute && (
          <div className="detail-item">
            <span className="detail-label">Preemption status</span>
            <PreemptionStatusBadge status={preemptionStatus} />
          </div>
        )}

        {/* 2. Calculated target speed -- read from outgoingSegment,
            computed server-side in acta_engine.py, never recomputed
            here with separate default parameters. */}
        <div className="detail-item">
          <span className="detail-label">Calculated target speed</span>
          {outgoingSegment?.target_speed ? (
            <span className="detail-value">
              {fmtSpeed(outgoingSegment.target_speed.kmh, outgoingSegment.target_speed.mps)}{" "}
              <span className="detail-value--muted">(Recommended Corridor Speed)</span>
            </span>
          ) : (
            <span className="detail-value detail-value--muted">
              &mdash; (no downstream segment to target)
            </span>
          )}
        </div>

        {/* Assigned ACTA case -- same outgoingSegment object that
            drives the breakdown below, so the tag and the breakdown
            can never disagree. */}
        <div className="detail-item">
          <span className="detail-label">Assigned ACTA case</span>
          {outgoingSegment ? (
            <span className={`tag ${outgoingSegment.case_type}`} style={{ width: "fit-content" }}>
              {CASE_LABELS[outgoingSegment.case_type] || outgoingSegment.case_type}
            </span>
          ) : (
            <span className="detail-value detail-value--muted">
              {discarded ? "Rejected \u2014 not scheduled" : "\u2014 (last signal on route)"}
            </span>
          )}
        </div>
        <div className="detail-item">
          <span className="detail-label">Distance to next signal</span>
          <span className="detail-value">
            {outgoingSegment ? fmtKm(outgoingSegment.distance_meters) : "\u2014 (last signal on route)"}
          </span>
        </div>

        {/* 3. Case-specific parameter breakdown, strictly derived from
            outgoingSegment.case_type via the switch in CaseBreakdown.
            Waypoint arrays are ONLY mapped/rendered inside the
            WAYPOINT_TRIGGER / MULTIPLE_WAYPOINTS branch of that
            switch -- MERGE_TRIGGER and IMMEDIATE_TRIGGER never reach
            that branch, so they can never leak a waypoint list. */}
        {onRoute && (
          <>
            <div className="detail-divider">
              Case breakdown
              {outgoingSegment && (
                <span className={`tag ${outgoingSegment.case_type}`} style={{ marginLeft: 8 }}>
                  {CASE_LABELS[outgoingSegment.case_type] || outgoingSegment.case_type}
                </span>
              )}
            </div>
            <CaseBreakdown segment={outgoingSegment} />
          </>
        )}

        <div className="detail-divider">Additional telemetry</div>
        <div className="detail-item">
          <span className="detail-label">Position on route</span>
          <span className="detail-value">{fmtKm(signal.position_on_route_meters)}</span>
        </div>
        {discarded && (
          <div className="detail-item">
            <span className="detail-label">Rejection reason</span>
            <span className="detail-value">{fmtReason(signal.reason)}</span>
          </div>
        )}
        {incomingSegment && (
          <div className="detail-item">
            <span className="detail-label">Distance from previous signal</span>
            <span className="detail-value">{fmtKm(incomingSegment.distance_meters)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
