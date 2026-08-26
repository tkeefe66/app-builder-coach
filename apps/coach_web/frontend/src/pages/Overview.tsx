import { useEffect, useState } from "react";
import { get } from "../api";
import { type Assessment, type Brief } from "../components/AssessmentCard";
import { type Recurring } from "../components/BriefHistory";
import GradeCard, { type Grade } from "../components/GradeCard";
import StatTile from "../components/StatTile";
import { fmtDate, relDays } from "../format";

type Briefs = {
  assessment: Assessment | null; deltas: Brief[]; history: Brief[];
  recurring: Recurring[];
};

type Overview = {
  freshness: { captured_at: string | null; received_at: string | null };
  tiles: { units_this_week: number; commits_this_week: number;
    streak_days: number; streak_last_active: string | null;
    sessions_this_week: number | null; cost_this_week: number | null };
  never_built: string[];
  stale: { tag: string; last_done: string }[];
  adoption_gaps: string[];
  active_goals: { id: number; kind: string; target: string; title: string;
    target_date: string }[];
  grade: Grade;
};

export default function Overview() {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState("");
  const [briefs, setBriefs] = useState<Briefs | null>(null);
  useEffect(() => { get("/api/overview").then(setData).catch((e) => setErr(String(e))); }, []);
  useEffect(() => { get("/api/briefs?limit=10").then(setBriefs).catch(() => setBriefs(null)); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const f = data.freshness.captured_at;
  return (
    <>
      <h1>Overview</h1>
      <p className="muted" style={{ marginTop: -8 }}>
        Data as of {f ? `${fmtDate(f.slice(0, 10))} (${relDays(f.slice(0, 10), new Date())})` : "—"}
      </p>
      <GradeCard grade={data.grade} />
      {briefs?.assessment && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>
            Assessment{" "}
            <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
              {fmtDate(briefs.assessment.day)}
            </span>
          </h2>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {briefs.assessment.summary}
          </p>
        </div>
      )}
      {data.active_goals?.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Active goals</h2>
          <ul className="ink2" style={{ fontSize: 13 }}>
            {data.active_goals.map((g) => (
              <li key={g.id}>{g.title}{" "}
                <span className="muted">({g.target}
                {g.target_date ? ` · by ${g.target_date}` : ""})</span></li>
            ))}
          </ul>
        </div>
      )}
      <div className="tile-row">
        <StatTile label="Features this week" value={data.tiles.units_this_week} />
        <StatTile label="Commits this week" value={data.tiles.commits_this_week} />
        <StatTile label="Streak" value={`${data.tiles.streak_days}d`}
          sub={data.tiles.streak_last_active
            ? `last active ${relDays(data.tiles.streak_last_active, new Date())}` : undefined} />
        <StatTile label="Sessions this week"
          value={data.tiles.sessions_this_week ?? "—"}
          sub={data.tiles.sessions_this_week === null ? "no data yet" : undefined}
          dim={data.tiles.sessions_this_week === null} />
        <StatTile label="Spend this week (est.)"
          value={data.tiles.cost_this_week === null ? "—" : `$${data.tiles.cost_this_week.toFixed(2)}`}
          sub={data.tiles.cost_this_week === null ? "no data yet" : undefined}
          dim={data.tiles.cost_this_week === null} />
      </div>
      <div className="tile-row" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Never built</h2>
          <ul className="ink2">{data.never_built.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Stale (6+ months)</h2>
          {data.stale.length === 0 && <p className="muted">Nothing stale.</p>}
          <ul className="ink2">{data.stale.map((s) => (
            <li key={s.tag}>{s.tag} <span className="muted">({fmtDate(s.last_done)})</span></li>
          ))}</ul>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Claude Code gaps</h2>
          <ul className="ink2">{data.adoption_gaps.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      </div>
    </>
  );
}
