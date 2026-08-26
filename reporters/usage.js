// Report Anthropic usage to coach-web. Copy this file into each app.
//
// Contract: never throws, never blocks. Every failure is swallowed and the POST
// is not awaited. No dependencies — uses global fetch (Node 18+).

const FIELDS = [
  "input_tokens",
  "output_tokens",
  "cache_read_input_tokens",
  "cache_creation_input_tokens",
];

export function buildPayload(app, model, usage, ts) {
  const counts = {};
  for (const field of FIELDS) {
    const value = usage == null ? 0 : usage[field];
    counts[field] = Number.isFinite(Number(value)) ? Number(value) : 0;
  }
  return {
    app,
    model: String(model),
    ts: ts || new Date().toISOString(),
    ...counts,
  };
}

export function report(app, model, usage, options = {}) {
  try {
    const url = options.url ?? process.env.COACH_USAGE_URL;
    const token = options.token ?? process.env.COACH_USAGE_TOKEN;
    if (!url || !token) return;
    // Not awaited: reporting must never add latency to the caller.
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(buildPayload(app, model, usage)),
      signal: AbortSignal.timeout(3000),
    })
      .then((resp) => {
        if (resp.ok) return;
        // A 4xx/5xx here means the report was rejected (e.g. an unknown
        // `app`) -- log it so a typo doesn't vanish silently, without
        // awaiting or throwing.
        return resp.text().catch(() => "").then((detail) => {
          console.warn(`coach-web usage report rejected: status=${resp.status} body=${detail}`);
        });
      })
      .catch(() => {});
  } catch {
    // a lost data point must never surface in the calling app
  }
}
