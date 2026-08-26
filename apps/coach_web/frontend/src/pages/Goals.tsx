import { useEffect, useState } from "react";
import { del, get, patch, post } from "../api";
import AssessmentCard, { type Assessment, type Brief, type Recommendation }
  from "../components/AssessmentCard";
import BriefHistory, { type Recurring } from "../components/BriefHistory";
import GoalPicker, { type Gaps, type Goal, type NewGoal }
  from "../components/GoalPicker";
import SinceThen from "../components/SinceThen";

type Briefs = {
  assessment: Assessment | null; deltas: Brief[]; history: Brief[];
  recurring: Recurring[];
};
type Dismissal = { id: number; kind: string; target: string; reason: string;
  created_at: string };

const NO_GAPS: Gaps = { never_built: [], stale: [], adoption_gaps: [] };

export default function Goals() {
  const [briefs, setBriefs] = useState<Briefs | null>(null);
  const [gaps, setGaps] = useState<Gaps>(NO_GAPS);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [dismissals, setDismissals] = useState<Dismissal[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const loadBriefs = () =>
    get("/api/briefs").then(setBriefs).catch((e) => setErr(String(e)));
  const loadGoals = () =>
    get("/api/goals").then((d) => setGoals(d.goals ?? [])).catch(() => {});
  const loadDismissals = () =>
    get("/api/dismissals").then((d) => setDismissals(d.dismissals ?? [])).catch(() => {});
  const loadGaps = () =>
    get("/api/overview").then((d) => setGaps({
      never_built: d.never_built ?? [],
      stale: (d.stale ?? []).map((s: { tag: string }) => s.tag),
      adoption_gaps: d.adoption_gaps ?? [],
    })).catch(() => {});

  useEffect(() => {
    loadBriefs(); loadGoals(); loadDismissals(); loadGaps();
  }, []);

  async function addGoal(goal: NewGoal) {
    await post("/api/goals", goal);
    // The write also converts matching recommendations, so both must reload.
    await Promise.all([loadGoals(), loadBriefs()]);
  }

  async function dismiss(rec: Recommendation) {
    await post("/api/dismissals", { kind: rec.kind, target: rec.target, reason: "" });
    await Promise.all([loadDismissals(), loadBriefs(), loadGaps()]);
  }

  async function undismiss(rec: Recommendation) {
    // The component's `dismissals` state can be stale (e.g. dismissed in
    // another tab), so re-fetch before looking up the id to delete.
    const fresh = await get("/api/dismissals");
    const match = (fresh.dismissals ?? []).find(
      (d: Dismissal) => d.kind === rec.kind && d.target === rec.target,
    );
    if (!match) return;
    await del(`/api/dismissals/${match.id}`);
    await Promise.all([loadDismissals(), loadBriefs(), loadGaps()]);
  }

  async function reassess() {
    setBusy(true);
    try {
      await post("/api/reassess", {});
      await loadBriefs();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Goals &amp; Coach</h1>
      {err && <p className="muted">Failed to load: {err}</p>}

      <AssessmentCard
        assessment={briefs?.assessment ?? null}
        onAdd={(rec) => addGoal({ kind: rec.kind, target: rec.target, title: rec.title })}
        onDismiss={dismiss}
        onUndismiss={undismiss}
        onReassess={reassess}
        busy={busy}
      />

      <SinceThen deltas={briefs?.deltas ?? []}
        since={briefs?.assessment?.day ?? null} />

      <GoalPicker
        goals={goals}
        gaps={gaps}
        onAdd={addGoal}
        onDone={async (id) => { await patch(`/api/goals/${id}`, { status: "done" }); loadGoals(); }}
        onDelete={async (id) => { await del(`/api/goals/${id}`); loadGoals(); }}
      />

      <BriefHistory recurring={briefs?.recurring ?? []}
        history={briefs?.history ?? []} />

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Dismissed</h2>
        <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
          Hidden from the coach's suggestions. Still counted in the gap lists on Overview.
        </p>
        {dismissals.length === 0 && (
          <p className="muted" style={{ fontSize: 13 }}>Nothing dismissed.</p>
        )}
        {dismissals.map((d) => (
          <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
            marginBottom: 6 }}>
            <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
              {d.target}{" "}
              <span className="muted">({d.kind}
              {d.reason ? ` · ${d.reason}` : ""})</span>
            </span>
            <button type="button" onClick={async () => {
              await del(`/api/dismissals/${d.id}`);
              await Promise.all([loadDismissals(), loadGaps()]);
            }}>Un-dismiss</button>
          </div>
        ))}
      </div>
    </>
  );
}
