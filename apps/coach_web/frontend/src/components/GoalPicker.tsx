export type Goal = {
  id: number; kind: string; target: string; title: string;
  target_date: string; status: string;
};

export type Gaps = {
  never_built: string[]; stale: string[]; adoption_gaps: string[];
};

export type NewGoal = { kind: string; target: string; title: string };

type Props = {
  goals: Goal[];
  gaps: Gaps;
  onAdd: (goal: NewGoal) => void;
  onDone: (id: number) => void;
  onDelete: (id: number) => void;
};

// There is deliberately no free-text field: a goal is always a taxonomy tag or
// a Claude Code feature, which is what the write API has always required.
export default function GoalPicker({ goals, gaps, onAdd, onDone, onDelete }: Props) {
  const active = goals.filter((g) => g.status === "active");
  const taken = new Set(active.map((g) => `${g.kind}:${g.target}`));

  const options: NewGoal[] = [
    ...gaps.never_built.map((t) => ({
      kind: "tag", target: t, title: `Build something with ${t}` })),
    ...gaps.stale.map((t) => ({
      kind: "tag", target: t, title: `Come back to ${t}` })),
    ...gaps.adoption_gaps.map((f) => ({
      kind: "feature", target: f, title: `Adopt ${f}` })),
  ].filter((o) => !taken.has(`${o.kind}:${o.target}`));

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>Goals</h2>
      {active.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>No active goals.</p>
      )}
      {active.map((g) => (
        <div key={g.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
          marginBottom: 6 }}>
          <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
            {g.title}{" "}
            <span className="muted">({g.target}
            {g.target_date ? ` · by ${g.target_date}` : ""})</span>
          </span>
          <button type="button" onClick={() => onDone(g.id)}>Done</button>
          <button type="button" onClick={() => onDelete(g.id)}>Delete</button>
        </div>
      ))}

      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer", fontSize: 13 }}>
          Add from gaps ({options.length})
        </summary>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {options.map((o) => (
            <button key={`${o.kind}:${o.target}`} type="button"
              onClick={() => onAdd(o)}>
              Add {o.target}
            </button>
          ))}
          {options.length === 0 && (
            <p className="muted" style={{ fontSize: 13 }}>
              Every known gap is already a goal.
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
