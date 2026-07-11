import React, { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts";

// Stub dashboard. Three panels: model inventory, per-agent accuracy
// trend, and the transparency report. All data comes from the backend;
// no values are hardcoded in the shipped views. Replace the fetch URLs
// once the governance endpoints are wired.
export default function App() {
  const [inventory, setInventory] = useState([]);
  const [trend, setTrend] = useState([]);
  const [report, setReport] = useState([]);

  useEffect(() => {
    fetch("/api/inventory").then((r) => r.json()).then(setInventory).catch(() => {});
    fetch("/api/accuracy").then((r) => r.json()).then(setTrend).catch(() => {});
    fetch("/api/transparency").then((r) => r.json()).then(setReport).catch(() => {});
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <h1>Care Ops Copilot</h1>
      <p style={{ color: "#555" }}>Governance and drift monitoring</p>

      <section>
        <h2>Model inventory</h2>
        <ul>
          {inventory.map((m, i) => (
            <li key={i}>{m.agent_name}: {m.model} ({m.version})</li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Per-agent accuracy over time</h2>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={trend}>
            <XAxis dataKey="window_label" />
            <YAxis domain={[0, 1]} />
            <Tooltip />
            <Line type="monotone" dataKey="accuracy" stroke="#2b6cb0" />
          </LineChart>
        </ResponsiveContainer>
      </section>

      <section>
        <h2>Transparency report</h2>
        {report.map((r, i) => (
          <div key={i} style={{ border: "1px solid #ddd", padding: 12, marginBottom: 8 }}>
            <strong>{r.agent_name}</strong> ({r.model} {r.version})
            <div>Intended use: {r.intended_use}</div>
            <div>Known limitations: {r.known_limitations}</div>
          </div>
        ))}
      </section>
    </main>
  );
}
