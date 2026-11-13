import { useMemo, useState } from "react";
import CorridorView from "./CorridorView";
import SignalsPanel from "./SignalsPanel";
import InspectionDrawer from "./InspectionDrawer";
import TelemetryLog from "./TelemetryLog";
import SimConfigFiles from "./SimConfigFiles";
import PlaceAutocompleteInput from "./PlaceAutocompleteInput";
import { buildTelemetry } from "./telemetry";
import { generateFullPlan, runSimulationForPlan } from "./api";
import { fmtKm, fmtSeconds } from "./format";

export default function App() {
  const [origin, setOrigin] = useState("Christ University Kengeri Campus");
  const [originPlaceId, setOriginPlaceId] = useState(null);
  const [destination, setDestination] = useState("Rajarajeshwari Nagar Metro Station");
  const [destinationPlaceId, setDestinationPlaceId] = useState(null);
  const [routingMode, setRoutingMode] = useState("fastest"); // "fastest" | "shortest"

  const [plan, setPlan] = useState(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState(null);

  const [compare, setCompare] = useState(true);
  const [sim, setSim] = useState(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simError, setSimError] = useState(null);

  const [corridorViewMode, setCorridorViewMode] = useState("table"); // "table" | "visualizer"
  const [selectedSignalId, setSelectedSignalId] = useState(null); // click-pinned
  const [hoveredSignalId, setHoveredSignalId] = useState(null); // transient hover preview
  const [showActivationPlan, setShowActivationPlan] = useState(false);

  // Hover takes priority while active (mouse over a node); otherwise the
  // last click-selected node stays pinned in the drawer.
  const activeSignalId = hoveredSignalId ?? selectedSignalId;

  // A manually-edited origin/destination string no longer corresponds
  // to whatever place_id was last resolved by Autocomplete for that
  // field -- clear the stale id so the backend re-geocodes the typed
  // text instead of silently routing to the old selection.
  function handleOriginPlaceChange({ placeId, formattedAddress }) {
    setOrigin(formattedAddress);
    setOriginPlaceId(placeId);
  }
  function handleDestinationPlaceChange({ placeId, formattedAddress }) {
    setDestination(formattedAddress);
    setDestinationPlaceId(placeId);
  }

  async function handleGeneratePlan(e) {
    e.preventDefault();
    setPlanLoading(true);
    setPlanError(null);
    setSim(null);
    setSimError(null);
    setSelectedSignalId(null);
    setHoveredSignalId(null);
    try {
      const result = await generateFullPlan({
        origin,
        destination,
        routingMode,
        originPlaceId,
        destinationPlaceId,
      });
      setPlan(result);
    } catch (err) {
      setPlanError(err.message);
      setPlan(null);
    } finally {
      setPlanLoading(false);
    }
  }

  async function handleRunSimulation() {
    setSimLoading(true);
    setSimError(null);
    try {
      const result = await runSimulationForPlan({ plan, compare, endTime: 1200 });
      setSim(result);
    } catch (err) {
      setSimError(err.message);
      setSim(null);
    } finally {
      setSimLoading(false);
    }
  }

  const telemetryEntries = useMemo(
    () => buildTelemetry({ plan, planError, sim, simError }),
    [plan, planError, sim, simError]
  );

  // "Efficiency boost" -- percentage of the baseline (no-preemption)
  // arrival time saved by running ACTA preemption. Only meaningful
  // once a comparison simulation has actually been run.
  const efficiencyBoostPct = useMemo(
    () =>
      sim?.time_saved_s != null && sim?.without_preemption_arrival_s
        ? (sim.time_saved_s / sim.without_preemption_arrival_s) * 100
        : null,
    [sim]
  );

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>ACTA<span>.</span> Corridor Preemption</h1>
          <div className="subtitle">
            Adaptive Corridor Trigger Algorithm — route matching &amp; SUMO signal-preemption simulation
          </div>
        </div>
        <span className="badge">FastAPI + SUMO/TraCI</span>
      </header>

      <form className="panel" onSubmit={handleGeneratePlan}>
        <h2>Route</h2>
        <div className="form-row">
          <div className="field">
            <label htmlFor="origin-autocomplete">Origin</label>
            <PlaceAutocompleteInput
              id="origin-autocomplete"
              value={origin}
              onPlaceChange={handleOriginPlaceChange}
              placeholder="Origin address (Bengaluru)"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="destination-autocomplete">Destination</label>
            <PlaceAutocompleteInput
              id="destination-autocomplete"
              value={destination}
              onPlaceChange={handleDestinationPlaceChange}
              placeholder="Destination address (Bengaluru)"
              required
            />
          </div>
          <div className="field">
            <label>&nbsp;</label>
            <span className="live-sync-badge" title="Route distance, ETA, and baseline arrival time are fetched live from Google's traffic-aware Routes API on every plan generation.">
              <span className="live-sync-dot" aria-hidden="true">🟢</span>
              Live Traffic Sync
            </span>
          </div>
          <button className="btn-primary" type="submit" disabled={planLoading || simLoading}>
            {planLoading ? "Matching..." : "Generate ACTA plan"}
          </button>
        </div>

        <div className="field routing-mode-field">
          <label>Routing preference</label>
          <div
            className="view-toggle routing-mode-toggle"
            role="radiogroup"
            aria-label="Routing preference"
          >
            <button
              type="button"
              role="radio"
              aria-checked={routingMode === "fastest"}
              className={`toggle-btn ${routingMode === "fastest" ? "active" : ""}`}
              onClick={() => setRoutingMode("fastest")}
              title="Google routingPreference=TRAFFIC_AWARE_OPTIMAL; SUMO duarouter default (travel-time) cost"
            >
              ⏱ Fastest time
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={routingMode === "shortest"}
              className={`toggle-btn ${routingMode === "shortest" ? "active" : ""}`}
              onClick={() => setRoutingMode("shortest")}
              title="Google routingPreference=TRAFFIC_UNAWARE (closest distance-minimizing proxy); SUMO duarouter --weight-attribute length"
            >
              📏 Shortest distance
            </button>
          </div>
        </div>

        {planError && <div className="error-box">{planError}</div>}
      </form>

      {plan && (
        <>
          <div className="kpi-panel">
            <div className="panel-header-row" style={{ marginBottom: 16 }}>
              <h2 style={{ margin: 0 }}>ACTA Efficiency Summary</h2>
              <span className="badge routing-mode-badge">
                {plan.routing_mode === "shortest" ? "📏 Shortest distance" : "⏱ Fastest time"}
              </span>
            </div>
            <div className="kpi-grid">
              <div className="kpi-card">
                <div className="kpi-value">{fmtKm(plan.route_distance_meters)}</div>
                <div className="kpi-label">Total route distance</div>
              </div>
              <div className="kpi-card">
                {plan.route_duration_in_traffic_seconds != null ? (
                  <div className="kpi-value kpi-live">{fmtSeconds(plan.route_duration_in_traffic_seconds)}</div>
                ) : (
                  <div className="kpi-value kpi-dim">Unavailable</div>
                )}
                <div className="kpi-label">Live traffic ETA (Google)</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-value kpi-ok">{plan.signals.length}</div>
                <div className="kpi-label">Active corridor signals</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-value kpi-warn">{plan.discarded_signals.length}</div>
                <div className="kpi-label">Rejected / off-route signals</div>
              </div>
              <div className="kpi-card">
                {sim?.time_saved_s !== undefined ? (
                  <div className="kpi-value kpi-accent">{sim.time_saved_s}s</div>
                ) : (
                  <div className="kpi-value kpi-dim">Run simulation</div>
                )}
                <div className="kpi-label">Time saved vs. baseline</div>
              </div>
              <div className="kpi-card kpi-card--highlight">
                {efficiencyBoostPct != null ? (
                  <div className="kpi-value kpi-accent">{efficiencyBoostPct.toFixed(1)}%</div>
                ) : (
                  <div className="kpi-value kpi-dim">Run simulation</div>
                )}
                <div className="kpi-label">Efficiency boost (arrival time)</div>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header-row">
              <h2>Corridor signals</h2>
              <div className="view-toggle">
                <button
                  type="button"
                  className={`toggle-btn ${corridorViewMode === "table" ? "active" : ""}`}
                  onClick={() => setCorridorViewMode("table")}
                >
                  Table view
                </button>
                <button
                  type="button"
                  className={`toggle-btn ${corridorViewMode === "visualizer" ? "active" : ""}`}
                  onClick={() => setCorridorViewMode("visualizer")}
                >
                  Corridor visualizer
                </button>
              </div>
            </div>

            {corridorViewMode === "table" ? (
              <SignalsPanel plan={plan} selectedId={selectedSignalId} onSelect={setSelectedSignalId} />
            ) : (
              <div className="corridor-with-drawer">
                <CorridorView
                  plan={plan}
                  simEvents={sim?.events}
                  selectedId={activeSignalId}
                  onSelect={setSelectedSignalId}
                  onHover={setHoveredSignalId}
                  etaSeconds={sim?.with_preemption_arrival_s ?? plan.route_duration_in_traffic_seconds ?? null}
                />
                <InspectionDrawer
                  plan={plan}
                  sim={sim}
                  selectedId={activeSignalId}
                  onClose={() => setSelectedSignalId(null)}
                />
              </div>
            )}
          </div>

          <div className="panel">
            <div className="telemetry-header" onClick={() => setShowActivationPlan((o) => !o)}>
              <div className="telemetry-title" style={{ color: "var(--text)" }}>
                ACTA activation plan (raw segments)
                <span style={{ color: "var(--text-dim)" }}>
                  ({plan.acta_plan.length} segments)
                </span>
              </div>
              <span className={`telemetry-chevron ${showActivationPlan ? "open" : ""}`}>&#9656;</span>
            </div>
            {showActivationPlan && (
              plan.acta_plan.length === 0 ? (
                <div className="empty-state">Fewer than two on-route signals — no segments to plan.</div>
              ) : (
                <table style={{ marginTop: 14 }}>
                  <colgroup>
                    <col className="col-narrow" />
                    <col className="col-narrow" />
                    <col className="col-narrow" />
                    <col className="col-mid" />
                    <col className="col-wide" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="col-narrow">From</th>
                      <th className="col-narrow">To</th>
                      <th className="col-narrow">Distance</th>
                      <th className="col-mid">Case</th>
                      <th className="col-wide">Trigger detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {plan.acta_plan.map((seg, i) => (
                      <tr key={i}>
                        <td className="col-narrow" title={String(seg.from_signal)}>{seg.from_signal}</td>
                        <td className="col-narrow" title={String(seg.to_signal)}>{seg.to_signal}</td>
                        <td className="col-narrow">{fmtKm(seg.distance_meters)}</td>
                        <td className="col-mid"><span className={`tag ${seg.case_type}`}>{seg.case_type.replaceAll("_", " ")}</span></td>
                        <td className="col-wide">
                          {(seg.case_type === "WAYPOINT_TRIGGER" || seg.case_type === "MULTIPLE_WAYPOINTS") &&
                            seg.spec_waypoints && (
                              <span
                                className="meta-badge"
                                title={seg.spec_waypoints.map((w) => `${w.id} @ ${fmtKm(w.position_on_route_meters)}`).join(", ")}
                              >
                                {seg.spec_waypoints.length} waypoints
                              </span>
                            )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            )}
          </div>

          <div className="panel">
            <h2>SUMO simulation</h2>
            <div className="checkbox-row" style={{ marginBottom: 14 }}>
              <input
                type="checkbox"
                id="compare"
                checked={compare}
                onChange={(e) => setCompare(e.target.checked)}
              />
              <label htmlFor="compare">Compare against a no-preemption baseline</label>
            </div>
            <button className="btn-secondary" onClick={handleRunSimulation} disabled={simLoading || planLoading}>
              {simLoading ? "Running SUMO..." : "Build & run SUMO simulation"}
            </button>
            {simError && <div className="error-box">{simError}</div>}

            {sim && (
              <div style={{ marginTop: 18 }}>
                <div className="stat-row">
                  <div className="stat">
                    <div className="value">{fmtSeconds(sim.with_preemption_arrival_s)}</div>
                    <div className="label">Arrival, with ACTA</div>
                  </div>
                  {sim.without_preemption_arrival_s !== undefined && (
                    <div className="stat">
                      <div className="value">{fmtSeconds(sim.without_preemption_arrival_s)}</div>
                      <div className="label">
                        Arrival, baseline
                        {sim.baseline_source === "google_live_traffic" && (
                          <span className="live-tag" title="Sourced from Google's live traffic-aware ETA, not a second SUMO run.">
                            🟢 live
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                  {sim.time_saved_s !== undefined && (
                    <div className="stat">
                      <div className="value">{sim.time_saved_s}s</div>
                      <div className="label">Time saved</div>
                    </div>
                  )}
                  <div className="stat">
                    <div className="value">{sim.events.filter((e) => e.event === "PREEMPT_ON").length}</div>
                    <div className="label">Preemptions fired</div>
                  </div>
                </div>

                <SimConfigFiles sim={sim} />
              </div>
            )}
          </div>

          <div className="panel">
            <h2>System telemetry</h2>
            <TelemetryLog entries={telemetryEntries} />
          </div>
        </>
      )}
    </div>
  );
}
