import { useEffect, useState } from "react";
import { get } from "../api";
import StatTile from "../components/StatTile";
import WeeklyBars from "../components/WeeklyBars";
import { relDays } from "../format";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type Activity = {
  weeks: { start: string; commits: number; by_repo: Record<string, number>;
    sessions?: number }[];
  weekday_totals: number[];
  streak: { days: number; last_active: string | null };
  sessions_available: boolean;
};

export default function Activity() {
  const [data, setData] = useState<Activity | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/activity?weeks=12").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const max = Math.max(1, ...data.weekday_totals);
  const thisWeek = data.weeks[data.weeks.length - 1];
  const repoTotals: Record<string, number> = {};
  for (const w of data.weeks) {
    for (const [r, n] of Object.entries(w.by_repo)) repoTotals[r] = (repoTotals[r] ?? 0) + n;
  }
  return (
    <>
      <h1>Activity</h1>
      <div className="tile-row">
        <StatTile label="Commits this week" value={thisWeek?.commits ?? 0} />
        <StatTile label="Streak" value={`${data.streak.days}d`}
          sub={data.streak.last_active
            ? `last active ${relDays(data.streak.last_active, new Date())}` : undefined} />
        {data.sessions_available
          ? <StatTile label="Sessions this week" value={thisWeek?.sessions ?? 0} />
          : <StatTile label="Sessions" value="—" sub="no data yet" dim />}
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Commits per week</h2>
        <WeeklyBars data={data.weeks} />
      </div>
      <div className="tile-row" style={{ marginTop: 16, gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>By weekday (12 weeks)</h2>
          {data.weekday_totals.map((n, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, margin: "4px 0" }}>
              <span className="muted" style={{ width: 32, fontSize: 12 }}>{DAYS[i]}</span>
              <div style={{ height: 12, borderRadius: 4, background: "var(--series-1)",
                width: `${(n / max) * 100}%`, minWidth: n > 0 ? 4 : 0 }} />
              <span className="num ink2" style={{ fontSize: 12 }}>{n}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>By repo (12 weeks)</h2>
          <table style={{ width: "100%", fontSize: 13 }}>
            <tbody>
              {Object.entries(repoTotals).sort((a, b) => b[1] - a[1]).map(([r, n]) => (
                <tr key={r}><td className="ink2">{r}</td>
                  <td className="num" style={{ textAlign: "right" }}>{n}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
