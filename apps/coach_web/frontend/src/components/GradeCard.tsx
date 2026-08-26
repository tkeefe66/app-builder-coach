import { relDays } from "../format";

type Gap = {
  tag: string;
  have: { count: number; avg_complexity: number | null; last_done: string | null };
  need: { min_count: number; min_avg_complexity: number | null; within_days: number | null };
  best_fit_repo: string;
};
export type Grade = {
  level: string; level_label: string;
  next_level: string | null; next_label: string | null;
  percent_to_next: number; gaps: Gap[];
} | null;

function gapLine(g: Gap): string {
  if (g.have.count === 0) return `${g.tag} (never built)`;
  const parts: string[] = [];
  if (g.have.count < g.need.min_count) {
    parts.push(`${g.have.count} builds — need ${g.need.min_count}+`);
  }
  if (g.need.min_avg_complexity !== null && g.have.avg_complexity !== null
      && g.have.avg_complexity < g.need.min_avg_complexity) {
    parts.push(`avg complexity ${g.have.avg_complexity} — need ${g.need.min_avg_complexity}+`);
  }
  if (parts.length === 0) {
    // Count and complexity are satisfied — the shortfall is pure recency.
    if (g.have.last_done !== null) {
      parts.push(`${g.have.count} builds, last done ${relDays(g.have.last_done, new Date())} — refresh it`);
    } else {
      // Cannot happen in practice (count > 0 implies last_done is set), but
      // TS strict needs an exhaustive fallback.
      parts.push(`${g.have.count} builds — need ${g.need.min_count}+`);
    }
  }
  return `${g.tag} (${parts.join("; ")})`;
}

export default function GradeCard({ grade }: { grade: Grade }) {
  if (!grade) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="muted" style={{ fontSize: 13 }}>Overall grade</div>
        <p className="muted">No data yet.</p>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="muted" style={{ fontSize: 13 }}>Operating at</div>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{grade.level_label}</div>
      {grade.next_label ? (
        <>
          <div className="progress-track" style={{ marginTop: 8 }}
            role="progressbar" aria-valuenow={grade.percent_to_next}
            aria-valuemin={0} aria-valuemax={100}>
            <div className="progress-fill"
              style={{ width: `${grade.percent_to_next}%` }} />
          </div>
          <div className="ink2 num" style={{ fontSize: 12, marginTop: 4 }}>
            {grade.percent_to_next}% to {grade.next_label}
          </div>
          {grade.gaps.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 13 }}>
                To reach {grade.next_label}, build:
              </div>
              <ul className="ink2" style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                {grade.gaps.slice(0, 3).map((g) => (
                  <li key={g.tag}>
                    {gapLine(g)}{" "}
                    <span className="muted">best fit: {g.best_fit_repo}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <div className="ink2" style={{ fontSize: 12, marginTop: 4 }}>
          Top of the ladder.
        </div>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        Based on what you've shipped across skill areas — not years of experience.
      </div>
    </div>
  );
}
