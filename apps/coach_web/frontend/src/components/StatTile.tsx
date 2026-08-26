export default function StatTile({ label, value, sub, dim, warn }: {
  label: string; value: string | number; sub?: string; dim?: boolean; warn?: boolean;
}) {
  return (
    <div className={warn ? "card tile-warn" : "card"}
      style={dim ? { opacity: 0.5 } : undefined}>
      <div className="muted" style={{ fontSize: 13 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{value}</div>
      {sub && <div className={warn ? "warn-text" : "ink2"} style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}
