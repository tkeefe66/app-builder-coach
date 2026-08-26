import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";

export default function Login() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const nav = useNavigate();
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await login(password)) nav("/");
    else setError("Wrong password (or rate-limited — wait a minute).");
  }
  return (
    <form onSubmit={submit} className="card" style={{ maxWidth: 320, margin: "20vh auto" }}>
      <h1>Build Coach</h1>
      <input type="password" value={password} autoFocus
        onChange={(e) => setPassword(e.target.value)} placeholder="Password"
        style={{ width: "100%", padding: 8, boxSizing: "border-box" }} />
      <button type="submit" style={{ marginTop: 12, padding: "8px 16px" }}>
        Sign in
      </button>
      {error && <p className="muted">{error}</p>}
    </form>
  );
}
