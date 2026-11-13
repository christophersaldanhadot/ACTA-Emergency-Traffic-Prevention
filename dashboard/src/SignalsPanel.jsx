import { Fragment, useMemo, useState, memo } from "react";
import { buildSignalRows } from "./signalRows";
import { fmtKm, fmtCoord, fmtCoordFull, fmtReason, CASE_LABELS } from "./format";

const PAGE_SIZE = 12;

const CASE_FILTERS = [
  { value: "ALL", label: "All cases" },
  { value: "IMMEDIATE_TRIGGER", label: "Immediate" },
  { value: "WAYPOINT_TRIGGER", label: "Waypoint" },
  { value: "MULTIPLE_WAYPOINTS", label: "Multi-waypoint" },
];

function SignalsPanel({ plan, selectedId, onSelect }) {
  const [search, setSearch] = useState("");
  // Default to ON_ROUTE-only: for a presentation, the corridor signals
  // that actually got preempted are the story. Rejected / off-route
  // signals are still one click away via "All statuses" -- they're
  // filtered out, not deleted.
  const [statusFilter, setStatusFilter] = useState("ON_ROUTE");
  const [caseFilter, setCaseFilter] = useState("ALL");
  const [page, setPage] = useState(1);
  const [expandedKey, setExpandedKey] = useState(null);

  const allRows = useMemo(() => buildSignalRows(plan), [plan]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allRows.filter((r) => {
      if (statusFilter !== "ALL" && r.status !== statusFilter) return false;
      if (caseFilter !== "ALL" && r.segment?.case_type !== caseFilter) return false;
      if (q && !String(r.signal_id).toLowerCase().includes(q)) return false;
      return true;
    });
  }, [allRows, statusFilter, caseFilter, search]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageRows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  function updateFilter(setter, value) {
    setter(value);
    setPage(1);
  }

  if (!plan.signals || (plan.signals.length === 0 && plan.discarded_signals.length === 0)) {
    return <div className="empty-state">No signals matched for this corridor.</div>;
  }

  return (
    <div className="signals-panel">
      <div className="filter-bar">
        <input
          className="filter-search"
          type="text"
          placeholder="Search signal ID..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <select value={statusFilter} onChange={(e) => updateFilter(setStatusFilter, e.target.value)}>
          <option value="ALL">All statuses</option>
          <option value="ON_ROUTE">On route</option>
          <option value="OFF_ROUTE">Off route</option>
        </select>
        <select value={caseFilter} onChange={(e) => updateFilter(setCaseFilter, e.target.value)}>
          {CASE_FILTERS.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
        <span className="filter-count">
          {filtered.length} of {allRows.length} signals
        </span>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">No signals match the current filters.</div>
      ) : (
        <>
          <table className="signals-table">
            <colgroup>
              <col className="col-idx" />
              <col className="col-signal" />
              <col className="col-status" />
              <col className="col-coord" />
              <col className="col-distance" />
              <col className="col-action" />
              <col className="col-params" />
            </colgroup>
            <thead>
              <tr>
                <th className="col-idx"></th>
                <th className="col-signal">Signal ID</th>
                <th className="col-status">Status</th>
                <th className="col-coord">Coordinates</th>
                <th className="col-distance">Distance</th>
                <th className="col-action">Action / Case</th>
                <th className="col-params">Parameters</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => {
                const isOpen = expandedKey === r.key;
                const isSelected = selectedId != null && String(selectedId) === String(r.signal_id);
                return (
                  <Fragment key={r.key}>
                    <tr
                      className={`sig-row ${r.status === "OFF_ROUTE" ? "discarded" : ""} ${isSelected ? "row-selected" : ""}`}
                      onClick={() => {
                        setExpandedKey(isOpen ? null : r.key);
                        onSelect?.(r.signal_id);
                      }}
                    >
                      <td className="col-idx">
                        <span className={`row-chevron ${isOpen ? "open" : ""}`}>&#9656;</span>
                      </td>
                      <td className="col-signal" title={String(r.signal_id)}>{r.signal_id}</td>
                      <td className="col-status">
                        <span className={`tag ${r.status === "ON_ROUTE" ? "on-route" : "off-route"}`}>
                          {r.status === "ON_ROUTE" ? "On route" : "Off route"}
                        </span>
                      </td>
                      <td className="col-coord coord" title={fmtCoordFull(r.latitude, r.longitude)}>
                        {fmtCoord(r.latitude, r.longitude)}
                      </td>
                      <td className="col-distance">{fmtKm(r.position_on_route_meters)}</td>
                      <td className="col-action">
                        {r.segment ? (
                          <span className={`tag ${r.segment.case_type}`}>{CASE_LABELS[r.segment.case_type] || r.segment.case_type}</span>
                        ) : r.status === "OFF_ROUTE" ? (
                          <span className="tag rejected">Rejected</span>
                        ) : (
                          <span className="tag dispatch">Dispatch</span>
                        )}
                      </td>
                      <td className="col-params">
                        <div className="chip-row">
                          {r.direction && <span className="chip">{r.direction.replaceAll("_", " ")}</span>}
                          {r.match_confidence != null && (
                            <span className="chip">conf {(r.match_confidence * 100).toFixed(0)}%</span>
                          )}
                          {(r.segment?.case_type === "WAYPOINT_TRIGGER" ||
                            r.segment?.case_type === "MULTIPLE_WAYPOINTS") &&
                            r.segment?.spec_waypoints && (
                              <span className="chip">{r.segment.spec_waypoints.length} waypoints</span>
                            )}
                          {r.reason && <span className="chip chip-warn">{fmtReason(r.reason)}</span>}
                        </div>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="detail-row">
                        <td colSpan={7}>
                          <div className="detail-drawer">
                            <div className="detail-item">
                              <span className="detail-label">Full coordinates</span>
                              <span className="detail-value coord">{fmtCoordFull(r.latitude, r.longitude)}</span>
                            </div>
                            <div className="detail-item">
                              <span className="detail-label">Distance from route</span>
                              <span className="detail-value">
                                {r.distance_from_route_meters != null ? `${r.distance_from_route_meters.toFixed(1)} m` : "-"}
                              </span>
                            </div>
                            {r.segment && (
                              <div className="detail-item">
                                <span className="detail-label">Triggered from</span>
                                <span className="detail-value">
                                  {r.segment.from_signal} &rarr; {r.segment.to_signal} ({fmtKm(r.segment.distance_meters)})
                                </span>
                              </div>
                            )}
                            {(r.segment?.case_type === "WAYPOINT_TRIGGER" ||
                              r.segment?.case_type === "MULTIPLE_WAYPOINTS") &&
                              r.segment?.spec_waypoints && (
                                <div className="detail-item">
                                  <span className="detail-label">Waypoint positions</span>
                                  <span className="detail-value">
                                    {r.segment.spec_waypoints
                                      .map((w) => fmtKm(w.position_on_route_meters))
                                      .join(", ")}
                                  </span>
                                </div>
                              )}
                            {r.reason && (
                              <div className="detail-item">
                                <span className="detail-label">Rejection reason</span>
                                <span className="detail-value">{fmtReason(r.reason)}</span>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>

          <div className="pagination">
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </button>
            <span className="page-counter">
              Page {safePage} of {pageCount} &middot; showing {pageRows.length ? (safePage - 1) * PAGE_SIZE + 1 : 0}
              &ndash;{(safePage - 1) * PAGE_SIZE + pageRows.length} of {filtered.length}
            </span>
            <button
              type="button"
              className="btn-secondary btn-sm"
              disabled={safePage >= pageCount}
              onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default memo(SignalsPanel);
