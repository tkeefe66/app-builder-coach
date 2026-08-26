import { Fragment, useEffect, useState } from "react";
import { get } from "../api";
import Empty from "../components/Empty";
import StatTile from "../components/StatTile";
import WeeklyBars from "../components/WeeklyBars";

type CostData = {
  available: boolean;
  days: { date: string; cost_usd: number; input_tokens: number;
    output_tokens: number; cache_read_tokens: number;
    cache_creation_tokens: number }[];
  weekly: { start: string; cost_usd: number }[];
  total_usd_window: number;
  by_model_window: Record<string, number>;
};

type TrueCost = {
  window_days: number;
  window: { start: string; end: string };
  apps: { app: string; display: string; railway_usd: number; llm_usd: number;
    total_usd: number; share: number;
    services?: { service: string; service_id: string; total_usd: number;
      memory_usd: number; share_of_app: number }[];
  }[];
  totals: { railway_usd: number; llm_usd: number; total_usd: number };
  railway_period: { period_start: string | null; to_date_usd: number };
  llm_month_to_date_usd: number;
  cache: { input_tokens: number; cache_read_tokens: number;
    cache_creation_tokens: number; read_ratio: number };
  drift: { from: string; to: string; console_usd: number; tracked_usd: number;
    gap_usd: number; age_days: number; stale: boolean } | null;
};

export default function Cost() {
  const [data, setData] = useState<CostData | null>(null);
  const [err, setErr] = useState("");
  const [tc, setTc] = useState<TrueCost | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => { get("/api/cost?weeks=12").then(setData).catch((e) => setErr(String(e))); }, []);
  useEffect(() => { get("/api/truecost?days=30").then(setTc).catch(() => setTc(null)); }, []);

  const tcReady = !!tc && tc.apps.length > 0;
  const tokens = data && data.available
    ? data.days.reduce((a, d) => a + d.input_tokens + d.output_tokens, 0)
    : 0;

  return (
    <>
      <h1>Cost</h1>
      {tcReady && tc && (
        <>
          <h2 style={{ fontSize: 16 }}>What your apps actually cost</h2>
          <p className="muted" style={{ fontSize: 12, marginTop: -8 }}>
            Trailing {tc.window_days} days ({tc.window.start} → {tc.window.end}).
            Real billed dollars, not an estimate.
          </p>
          <div className="tile-row">
            <StatTile label={`Total (${tc.window_days}d)`}
              value={`$${tc.totals.total_usd.toFixed(2)}`} />
            <StatTile label="Railway" value={`$${tc.totals.railway_usd.toFixed(2)}`}
              sub={tc.railway_period.period_start
                ? `$${tc.railway_period.to_date_usd.toFixed(2)} this billing period`
                : undefined} />
            <StatTile label="Anthropic API" value={`$${tc.totals.llm_usd.toFixed(2)}`}
              sub={`$${tc.llm_month_to_date_usd.toFixed(2)} month to date`} />
            <StatTile label="Untracked spend"
              value={tc.drift ? `$${tc.drift.gap_usd.toFixed(2)}` : "—"}
              sub={tc.drift
                ? (tc.drift.stale
                    ? `vs Console ${tc.drift.from}→${tc.drift.to} · ${tc.drift.age_days}d old, re-anchor`
                    : `vs Console ${tc.drift.from}→${tc.drift.to}`)
                : "not configured"}
              dim={!tc.drift}
              warn={!!tc.drift?.stale} />
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>By app</h2>
            <table style={{ width: "100%", fontSize: 13 }}>
              <thead>
                <tr className="muted">
                  <th style={{ textAlign: "left" }}>App</th>
                  <th style={{ textAlign: "right" }}>Railway</th>
                  <th style={{ textAlign: "right" }}>API</th>
                  <th style={{ textAlign: "right" }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {tc.apps.map((a) => {
                  const hasServices = !!a.services && a.services.length > 0;
                  const isOpen = !!open[a.app];
                  return (
                    <Fragment key={a.app}>
                      <tr>
                        <td className="ink2">
                          {hasServices ? (
                            <button type="button"
                              onClick={() => setOpen((o) => ({ ...o, [a.app]: !o[a.app] }))}
                              aria-expanded={isOpen}
                              style={{ background: "none", border: "none", padding: 0,
                                cursor: "pointer", color: "inherit", font: "inherit" }}>
                              {isOpen ? "▾" : "▸"} {a.display}
                            </button>
                          ) : a.display}
                        </td>
                        <td className="num" style={{ textAlign: "right" }}>${a.railway_usd.toFixed(2)}</td>
                        <td className="num" style={{ textAlign: "right" }}>${a.llm_usd.toFixed(2)}</td>
                        <td className="num" style={{ textAlign: "right" }}>${a.total_usd.toFixed(2)}</td>
                      </tr>
                      {isOpen && a.services!.map((s) => (
                        <tr key={s.service_id} className="muted" style={{ fontSize: 12 }}>
                          <td style={{ paddingLeft: 24 }}>
                            {s.service} <span className="ink2">
                              ({(s.share_of_app * 100).toFixed(0)}%)</span>
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>${s.total_usd.toFixed(2)}</td>
                          <td colSpan={2} style={{ textAlign: "right" }}>
                            memory ${s.memory_usd.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>Cache efficiency</h2>
            <p className="ink2" style={{ fontSize: 13 }}>
              {(tc.cache.read_ratio * 100).toFixed(1)}% of input tokens were served from
              cache ({tc.cache.cache_read_tokens.toLocaleString()} cached vs{" "}
              {tc.cache.input_tokens.toLocaleString()} uncached). Cache reads bill at
              roughly a tenth of uncached input.
            </p>
          </div>
        </>
      )}
      <h2 style={{ fontSize: 16, marginTop: 32 }}>
        Claude Code (estimated) <span className="muted" style={{ fontSize: 13 }}>(est. API value)</span>
      </h2>
      <p className="muted" style={{ fontSize: 12, marginTop: -8 }}>
        Subscription usage priced at API rates. Not a bill — you do not pay this.
      </p>
      {err ? (
        <p className="muted">Failed to load: {err}</p>
      ) : !data ? (
        <p className="muted">Loading…</p>
      ) : !data.available ? (
        <Empty title="No cost data yet"
          body="Run a sweep after Phase 3 ships locally — the usage lane fills this page." />
      ) : (
        <>
          <div className="tile-row">
            <StatTile label="12-week est. spend" value={`$${data.total_usd_window.toFixed(2)}`} />
            <StatTile label="In+out tokens (12w)" value={tokens.toLocaleString()} />
            <StatTile label="Models used" value={Object.keys(data.by_model_window).length} />
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>Est. spend per week</h2>
            <WeeklyBars allowDecimals name="est. USD"
              data={data.weekly.map((w) => ({ start: w.start, commits: w.cost_usd }))} />
            <p className="muted" style={{ fontSize: 12 }}>
              Y axis is USD (est.) — API-equivalent pricing applied to subscription usage.
            </p>
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <h2 style={{ marginTop: 0, fontSize: 15 }}>By model (12 weeks)</h2>
            <table style={{ width: "100%", fontSize: 13 }}>
              <tbody>
                {Object.entries(data.by_model_window).sort((a, b) => b[1] - a[1])
                  .map(([m, usd]) => (
                    <tr key={m}><td className="ink2">{m}</td>
                      <td className="num" style={{ textAlign: "right" }}>${usd.toFixed(2)}</td></tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
