export type Recommendation = {
  id: number; title: string; kind: string; target: string;
  why: string; evidence: string; outcome: string;
};

type Props = {
  rec: Recommendation;
  onAdd: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
  onUndismiss?: (rec: Recommendation) => void;
};

const STATUS_LABEL: Record<string, string> = {
  converted: "Added as a goal",
  dismissed: "Dismissed",
  superseded: "Superseded",
};

export default function RecommendationCard({ rec, onAdd, onDismiss, onUndismiss }: Props) {
  return (
    <div style={{ borderTop: "1px solid var(--grid)", paddingTop: 10, marginTop: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <strong style={{ fontSize: 14, flex: 1 }}>{rec.title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>{rec.target}</span>
      </div>
      <p className="ink2" style={{ fontSize: 13, margin: "6px 0 4px" }}>{rec.why}</p>
      <p className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>{rec.evidence}</p>
      {rec.outcome === "open" ? (
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" onClick={() => onAdd(rec)}>Add as goal</button>
          <button type="button" onClick={() => onDismiss(rec)}>Dismiss</button>
        </div>
      ) : rec.outcome === "dismissed" && onUndismiss ? (
        <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            {STATUS_LABEL[rec.outcome] ?? rec.outcome}
          </p>
          <button type="button" onClick={() => onUndismiss(rec)}>Undo</button>
        </div>
      ) : (
        <p className="muted" style={{ fontSize: 12 }}>
          {STATUS_LABEL[rec.outcome] ?? rec.outcome}
        </p>
      )}
    </div>
  );
}
