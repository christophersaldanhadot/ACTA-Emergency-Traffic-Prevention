import { useState } from "react";

// Raw Windows/SUMO file paths are long and low-signal on their own --
// wrapping them in a collapsed-by-default drawer keeps the "arrival /
// time saved / preemptions" stats as the visual focus, while the paths
// stay one click away for anyone who actually needs to open the SUMO
// GUI locally. Each path is also rendered as a small monospace badge
// with its own copy button, since selecting a raw path by hand (with
// backslashes) inside a scrolling panel is fiddly.
function PathBadge({ label, path }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(path);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) -- fail silently,
      // the path is still visible and selectable by hand.
    }
  }

  return (
    <div className="path-badge">
      <span className="path-badge-label">{label}</span>
      <code className="path-badge-value" title={path}>{path}</code>
      <button
        type="button"
        className="path-badge-copy"
        onClick={handleCopy}
        title={`Copy ${label} path`}
        aria-label={`Copy ${label} path`}
      >
        {copied ? "✓ Copied" : "⧉ Copy"}
      </button>
    </div>
  );
}

export default function SimConfigFiles({ sim }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="sim-config-drawer">
      <div className="telemetry-header" onClick={() => setOpen((o) => !o)}>
        <div className="telemetry-title" style={{ color: "var(--text)" }}>
          Simulation configuration files
          <span style={{ color: "var(--text-dim)" }}>(3 files)</span>
        </div>
        <span className={`telemetry-chevron ${open ? "open" : ""}`}>&#9656;</span>
      </div>
      {open && (
        <div className="sim-config-body">
          <PathBadge label="net" path={sim.net_file} />
          <PathBadge label="routes" path={sim.route_file} />
          <PathBadge label="sumocfg" path={sim.sumocfg_file} />
          <div className="sim-config-hint">
            Open in the SUMO GUI locally with:{" "}
            <code>sumo-gui -c {sim.sumocfg_file}</code>
          </div>
        </div>
      )}
    </div>
  );
}
