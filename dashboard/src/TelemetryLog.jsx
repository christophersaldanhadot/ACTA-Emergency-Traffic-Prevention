import { useState, memo } from "react";

function TelemetryLog({ entries }) {
  const [open, setOpen] = useState(true);

  const errorCount = entries.filter((e) => e.level === "ERROR").length;
  const warnCount = entries.filter((e) => e.level === "WARN").length;

  const dotColor = errorCount > 0 ? "var(--error)" : warnCount > 0 ? "var(--warn)" : "var(--ok)";

  return (
    <div className="telemetry-drawer">
      <div className="telemetry-header" onClick={() => setOpen((o) => !o)}>
        <div className="telemetry-title">
          <span className="telemetry-dot" style={{ background: dotColor }} />
          SYSTEM TELEMETRY LOG
          <span style={{ color: "var(--text-dim)" }}>
            ({entries.length} entries{warnCount > 0 ? `, ${warnCount} warn` : ""}{errorCount > 0 ? `, ${errorCount} error` : ""})
          </span>
        </div>
        <span className={`telemetry-chevron ${open ? "open" : ""}`}>&#9656;</span>
      </div>
      {open && (
        <div className="telemetry-body">
          {entries.map((e, i) => (
            <div key={i} className="telemetry-line">
              <span className={`telemetry-level level-${e.level}`}>[{e.level}]</span>
              <span className="telemetry-msg">{e.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default memo(TelemetryLog);
