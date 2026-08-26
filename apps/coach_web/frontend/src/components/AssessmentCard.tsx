import { fmtDate } from "../format";
import RecommendationCard, { type Recommendation } from "./RecommendationCard";

export type { Recommendation };

export type Brief = {
  created_at: string; day: string; kind: string; status: string;
  summary: string; error: string; recommendations: Recommendation[];
};

export type Assessment = Brief & { stale: boolean };

type Props = {
  assessment: Assessment | null;
  onAdd: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
  onUndismiss?: (rec: Recommendation) => void;
  onReassess: () => void;
  busy: boolean;
};

export default function AssessmentCard(
  { assessment, onAdd, onDismiss, onUndismiss, onReassess, busy }: Props,
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ marginTop: 0, fontSize: 15, flex: 1 }}>
          Assessment{" "}
          {assessment && (
            <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
              {fmtDate(assessment.day)}
            </span>
          )}
        </h2>
        <button type="button" onClick={onReassess} disabled={busy}>
          {busy ? "Reassessing…" : "Reassess"}
        </button>
      </div>

      {!assessment && (
        <>
          <p style={{ fontSize: 14, margin: "4px 0" }}>No assessment yet</p>
          <p className="muted" style={{ fontSize: 13 }}>
            One is written after the next sweep, or press Reassess to build it now.
          </p>
        </>
      )}

      {assessment && (
        <>
          {assessment.stale && (
            <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
              This assessment could not be regenerated — showing the last one that
              worked.{assessment.error ? ` (${assessment.error})` : ""}
            </p>
          )}
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {assessment.summary}
          </p>
          {assessment.recommendations.map((r) => (
            <RecommendationCard key={r.id} rec={r} onAdd={onAdd} onDismiss={onDismiss}
              onUndismiss={onUndismiss} />
          ))}
        </>
      )}
    </div>
  );
}
