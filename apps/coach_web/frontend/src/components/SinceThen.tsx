import { fmtDate } from "../format";
import type { Brief } from "./AssessmentCard";

export default function SinceThen(
  { deltas, since }: { deltas: Brief[]; since: string | null },
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>Since then</h2>
      {deltas.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>
          {since ? `No material change since ${fmtDate(since)}.`
            : "Nothing yet."}
        </p>
      )}
      {deltas.map((d) => (
        <div key={d.created_at} style={{ marginBottom: 12 }}>
          <p className="muted" style={{ fontSize: 12, marginBottom: 2 }}>
            {fmtDate(d.day)}
            {d.status === "failed" ? " — generation failed" : ""}
          </p>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap",
            marginTop: 0 }}>
            {d.status === "failed" ? d.error : d.summary}
          </p>
        </div>
      ))}
    </div>
  );
}
