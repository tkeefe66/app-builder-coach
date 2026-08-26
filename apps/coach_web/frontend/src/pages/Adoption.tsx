import { useEffect, useState } from "react";
import { del, get, post } from "../api";
import StatusChip from "../components/StatusChip";
import { fmtDate } from "../format";

type Feature = { name: string; lesson: string; status: string;
  last_used: string | null; source: string; discovered_at: string;
  checked_off?: boolean; detected_status?: string;
  dismissed: boolean; dismissal_id: number | null;
  history: { captured_at: string; status: string }[] };

export default function Adoption() {
  const [data, setData] = useState<{ features: Feature[] } | null>(null);
  const [err, setErr] = useState("");
  const reload = () => get("/api/adoption/board").then(setData).catch((e) => setErr(String(e)));
  useEffect(() => { reload(); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const lessons = [...new Set(data.features.map((f) => f.lesson))];
  const fresh = data.features.filter((f) => f.source === "changelog");
  return (
    <>
      <h1>Claude Code adoption</h1>
      {fresh.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>New since last check</h2>
          <p className="ink2">{fresh.map((f) => f.name).join(" · ")}</p>
        </div>
      )}
      {lessons.map((lesson) => (
        <div className="card" style={{ marginBottom: 12 }} key={lesson}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>{lesson}</h2>
          <table style={{ width: "100%", fontSize: 14, borderCollapse: "collapse" }}>
            <tbody>
              {data.features.filter((f) => f.lesson === lesson).map((f) => (
                <tr key={f.name} style={{ borderTop: "1px solid var(--grid)" }}>
                  <td style={{ padding: "8px 0", width: "40%" }}>{f.name}</td>
                  <td><StatusChip status={f.status} /></td>
                  <td className="ink2" style={{ textAlign: "right" }}>
                    {f.last_used ? `last used ${fmtDate(f.last_used)}` : ""}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button type="button" onClick={async () => {
                      if (f.checked_off) {
                        await del(`/api/checkoffs/${encodeURIComponent(f.name)}`);
                      } else {
                        await post("/api/checkoffs", { feature_name: f.name });
                      }
                      reload();
                    }}>{f.checked_off ? "Undo check off" : "Check off"}</button>{" "}
                    <button type="button" onClick={async () => {
                      if (f.dismissed && f.dismissal_id != null) {
                        await del(`/api/dismissals/${f.dismissal_id}`);
                      } else {
                        await post("/api/dismissals", { kind: "feature", target: f.name });
                      }
                      reload();
                    }}>{f.dismissed ? "Undo dismiss" : "Dismiss"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}
