import React, { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";

// P4-1: model inventory, a per-agent accuracy trend chart, active drift
// alerts, and one transparency report, all from the P3-5 governance
// endpoints. Nothing here is hardcoded: every value on the page traces back
// to one of the three fetches below, and an agent with no accuracy metric is
// shown as exactly that, never plotted as a 0 or a straight line.

const ENDPOINTS = {
  inventory: "/governance/inventory",
  trend: "/governance/accuracy-trend",
  report: "/governance/transparency-report",
};

function useGovernanceData(path) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch(path)
      .then((r) => {
        if (!r.ok) throw new Error(`${path} returned ${r.status}`);
        return r.json();
      })
      .then((body) => { if (!cancelled) setData(body); })
      .catch((err) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; };
  }, [path]);

  return { data, error };
}

// Rows whose validation text opens with the DRIFT verdict, and only that
// verdict: NO_DRIFT, NOT_ATTRIBUTABLE, and NOT_COMPARABLE all start with a
// different word, so a plain prefix check cannot mistake them for an active
// alert. See governance/drift.py::DriftVerdict for the four values this
// string can start with.
function isActiveDriftAlert(validationText) {
  return typeof validationText === "string" &&
    (validationText === "DRIFT" || validationText.startsWith("DRIFT:"));
}

const VALIDATION_CATEGORY =
  "Updates and continued validation or fairness assessment schedule";

function InventorySection({ data, error }) {
  return (
    <section>
      <h2>Model inventory</h2>
      {error && <p className="error">Could not load inventory: {error}</p>}
      {!error && data === null && <p className="muted">Loading...</p>}
      {data && data.length === 0 && (
        <p className="muted">No models registered yet.</p>
      )}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Agent</th><th>Model</th><th>Version</th>
              <th>Intended use</th><th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {data.map((m) => (
              <tr key={`${m.agent_name}-${m.model}-${m.version}`}>
                <td>{m.agent_name}</td>
                <td>{m.model}</td>
                <td>{m.version}</td>
                <td>{m.intended_use ?? "-"}</td>
                <td>{new Date(m.updated_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AccuracyTrendSection({ data, error }) {
  const scoreable = {};    // "agent (dataset)" -> rows with a non-null f1
  const unscoreable = new Set();

  for (const row of data ?? []) {
    if (row.f1 !== null && row.f1 !== undefined) {
      // Grouped by dataset, not just agent: two windows can share a
      // window_label (e.g. "v1") while scoring different held-out sets
      // (ACI-Bench n=120 vs PriMock57 n=7, see ROADMAP P3-1's backfill
      // entry). A line drawn across those would imply one continuous
      // series where there are actually two unrelated single measurements.
      const key = `${row.agent_name} (${row.dataset_ref})`;
      (scoreable[key] ??= []).push(row);
    } else {
      unscoreable.add(row.agent_name);
    }
  }

  return (
    <section>
      <h2>Per-agent accuracy over time</h2>
      {error && <p className="error">Could not load accuracy trend: {error}</p>}
      {!error && data === null && <p className="muted">Loading...</p>}

      {Object.entries(scoreable).map(([seriesName, rows]) => (
        <div key={seriesName}>
          <h3>{seriesName}</h3>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={rows}>
              <XAxis dataKey="window_label" />
              <YAxis domain={[0, 1]} />
              <Tooltip
                formatter={(value, name) => [Number(value).toFixed(4), name]}
              />
              <Line type="monotone" dataKey="f1" name="f1" stroke="#2b6cb0" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ))}

      {unscoreable.size > 0 && (
        <p className="muted">
          No accuracy metric to plot for: {[...unscoreable].join(", ")}.
          See each agent's "Quantitative measures of performance" in the
          transparency report below for what is measured instead.
        </p>
      )}
      {data && data.length === 0 && (
        <p className="muted">No evaluation runs recorded yet.</p>
      )}
    </section>
  );
}

function DriftAlertsSection({ report, error }) {
  const alerts = (report ?? []).filter(
    (row) => isActiveDriftAlert(row[VALIDATION_CATEGORY]));

  return (
    <section>
      <h2>Active drift alerts</h2>
      {error && <p className="error">Could not load drift status: {error}</p>}
      {!error && report === null && <p className="muted">Loading...</p>}
      {report && alerts.length === 0 && (
        <p className="muted">
          No active drift alerts. This reports the DRIFT verdict only; a
          delta can be measured and still read NOT_ATTRIBUTABLE, which does
          not appear here on purpose (see the transparency report below).
        </p>
      )}
      {alerts.length > 0 && (
        <ul>
          {alerts.map((row) => (
            <li key={row.agent_name} className="alert">
              <strong>{row.agent_name}</strong> ({row.model}):{" "}
              {row[VALIDATION_CATEGORY]}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function TransparencySection({ data, error }) {
  return (
    <section>
      <h2>Transparency report</h2>
      {error && <p className="error">Could not load transparency report: {error}</p>}
      {!error && data === null && <p className="muted">Loading...</p>}
      {data && data.map((r) => (
        <div className="card" key={`${r.agent_name}-${r.model}`}>
          <strong>{r.agent_name}</strong> ({r.model})
          <div>Purpose: {r["Purpose of the intervention"] ?? "-"}</div>
          <div>
            Cautioned out-of-scope use:{" "}
            {r["Cautioned out-of-scope use of the intervention"] ?? "-"}
          </div>
          <div>
            Quantitative performance:{" "}
            {r["Quantitative measures of performance"] ?? "-"}
          </div>
          <div>Ongoing maintenance: {r[VALIDATION_CATEGORY] ?? "-"}</div>
        </div>
      ))}
    </section>
  );
}

export default function App() {
  const inventory = useGovernanceData(ENDPOINTS.inventory);
  const trend = useGovernanceData(ENDPOINTS.trend);
  const report = useGovernanceData(ENDPOINTS.report);

  return (
    <main style={{ fontFamily: "system-ui", padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <h1>Care Ops Copilot</h1>
      <p className="muted">Governance and drift monitoring</p>

      <InventorySection data={inventory.data} error={inventory.error} />
      <AccuracyTrendSection data={trend.data} error={trend.error} />
      <DriftAlertsSection report={report.data} error={report.error} />
      <TransparencySection data={report.data} error={report.error} />
    </main>
  );
}
