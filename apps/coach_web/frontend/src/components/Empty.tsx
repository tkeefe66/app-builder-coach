export default function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="card" style={{ textAlign: "center", padding: 48 }}>
      <h2 style={{ margin: 0 }}>{title}</h2>
      <p className="muted">{body}</p>
    </div>
  );
}
