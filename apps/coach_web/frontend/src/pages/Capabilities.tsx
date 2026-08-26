import { useEffect, useState } from "react";
import { get } from "../api";
import Sparkline from "../components/Sparkline";
import { fmtDate } from "../format";

type TagRow = { tag: string; count: number; last_done: string;
  avg_complexity: number; monthly: { month: string; count: number }[] };

export default function Capabilities() {
  const [data, setData] = useState<{ tags: TagRow[]; never_built: string[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/capabilities").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Capabilities</h1>
      <div className="card" style={{ overflowX: "auto", padding: 0 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr className="muted" style={{ textAlign: "left" }}>
              <th style={{ padding: 12 }}>Tag</th>
              <th className="num">Features</th>
              <th>Last done</th>
              <th className="num">Avg complexity</th>
              <th>12-month trend</th>
            </tr>
          </thead>
          <tbody>
            {data.tags.map((t) => (
              <tr key={t.tag} style={{ borderTop: "1px solid var(--grid)" }}>
                <td style={{ padding: 12, fontWeight: 600 }}>{t.tag}</td>
                <td className="num">{t.count}</td>
                <td className="ink2">{fmtDate(t.last_done)}</td>
                <td className="num">{t.avg_complexity.toFixed(1)}</td>
                <td><Sparkline data={t.monthly} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Never built</h2>
        <p className="ink2">{data.never_built.join(" · ") || "—"}</p>
      </div>
    </>
  );
}
