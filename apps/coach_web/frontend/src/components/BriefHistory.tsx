import { fmtDate } from "../format";
import type { Brief } from "./AssessmentCard";

export type Recurring = {
  target: string; kind: string; times: number;
  first: string; last: string; outcome: string;
};

function fate(outcome: string): string {
  if (outcome === "converted") return "became a goal";
  if (outcome === "dismissed") return "dismissed";
  return "never acted on";
}

export default function BriefHistory(
  { recurring, history }: { recurring: Recurring[]; history: Brief[] },
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>History</h2>

      {recurring.length > 0 && (
        <>
          <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
            Recurring suggestions — what the coach keeps coming back to.
          </p>
          {recurring.map((r) => (
            <p key={`${r.kind}:${r.target}`} className="ink2"
              style={{ fontSize: 13, margin: "0 0 4px" }}>
              <strong>{r.target}</strong> — suggested {r.times}× between{" "}
              {fmtDate(r.first)} and {fmtDate(r.last)}, {fate(r.outcome)}
            </p>
          ))}
        </>
      )}

      {recurring.length === 0 && history.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>Nothing in the history yet.</p>
      )}

      {history.map((h) => (
        <details key={h.created_at} style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: 13 }}>
            <span className="muted">{fmtDate(h.day)}</span>{" "}
            {h.status === "failed"
              ? "generation failed"
              : h.recommendations.map((r) => r.target).join(" · ") || "no recommendations"}
          </summary>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {h.status === "failed" ? h.error : h.summary}
          </p>
          {h.recommendations.map((r) => (
            <p key={r.id} className="muted" style={{ fontSize: 12, margin: "0 0 4px" }}>
              {r.title} → {r.target}
            </p>
          ))}
        </details>
      ))}
    </div>
  );
}
