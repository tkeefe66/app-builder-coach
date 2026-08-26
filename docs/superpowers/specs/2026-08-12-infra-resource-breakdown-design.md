# Infra resource breakdown: per-service Railway cost (design)

**Status:** draft, 2026-08-12. Extends the True Cost design (`2026-08-11-true-cost-design.md`,
shipped as schema v3). Supersedes nothing.

**Goal.** Inside a Railway project, show which *service* is spending the money — app process
vs Postgres vs any other service — and which resource (memory, CPU, egress, volume, backup)
drives each service's cost. Answers "what inside this app costs the money," not "what does
this app cost" — the dashboard already answers that.

**Non-goal.** A general Railway metrics explorer. No new chart types, no per-service time
series, no alerting. This is a drill-down on numbers the dashboard already shows, not a new
surface.

## Why this is worth doing

The existing infra lane can say "b2b-ai-news costs $2.90" and stop there. Two facts make that
insufficient:

A full per-service sweep on 2026-08-11 (all 9 registry projects, 24 services) established the
baseline this feature exists to expose:

- **Memory is 97% of the entire Railway bill** — $11.50 of $11.88. CPU is 3%. Every single
  service is 93–100% memory. "Reduce memory spend" is not an actionable instruction today;
  "downsize *this* service" is, and the dashboard cannot name the service.
- **The split is 17 app services ($8.70, 73%) against 7 databases ($3.18, 27%).** That ratio
  is invisible from the current per-project totals, and it is the ratio that decides which
  lever to pull — app-level sleeping versus database consolidation are entirely different
  pieces of work.
- **Composition varies sharply by project, so the per-project total actively misleads.** On
  `B2B AI News`, Postgres is $1.86 of $2.94 — the database costs more than the app. On
  `app-builder-coach`, the reverse: the app is $0.234 against Postgres at $0.131. Same
  top-line shape, opposite conclusions. A reader of today's dashboard cannot tell these two
  cases apart without opening the Railway dashboard by hand, once per project.

The unit that can actually be acted on — resized, migrated, or turned off — is a service, not
a project. This feature closes the gap between "which app costs the most" (already answered)
and "which *thing inside that app* costs the most" (currently invisible).

## Why this shape

`railway usage projects --project "<id>" --json` returns exactly what's missing: a
`services` array with `memoryDollars`, `cpuDollars`, `egressDollars`, `volumeDollars`, and
`backupDollars` per service, at the same period-to-date cumulative granularity the existing
aggregate call already ships. Verified against the real CLI (`railway usage projects --help`):
`--project` accepts **"Project name or ID"** — so `apps.yaml`'s existing `railway_project_id`
field is the argument directly, with no new registry field. Confirmed live against the
`app-builder-coach` project:

```json
{"billingPeriod": {"start": "2026-07-27T16:07:00+00:00", "end": "2026-08-27T16:07:00+00:00"},
 "currentUsageDollars": 0.36497524407807735,
 "project": {"id": "9a0fc543-5688-4b67-be19-4ac7f09650f4", "name": "app-builder-coach"},
 "services": [
   {"id": "fbd1f9bb-...", "name": "coach-web", "totalDollars": 0.2338178478491236,
    "memoryDollars": 0.22549143752072853, "cpuDollars": 0.007157924228395061,
    "egressDollars": 0.0011684861, "volumeDollars": -0.0, "backupDollars": -0.0},
   {"id": "9475533f-...", "name": "Postgres", "totalDollars": 0.13115739622895378,
    "memoryDollars": 0.12309749295244449, "cpuDollars": 0.0012333200540123458,
    "egressDollars": 0.0, "volumeDollars": 0.006826583222496938, "backupDollars": 0.0}]}
```

**One call per project, not one call total.** The existing aggregate lane
(`railway usage projects --json`, no `--project`) returns every project's dollar total in a
single call and stays exactly as-is — it is still the cheapest way to get the top-line numbers
the dashboard already renders. This feature adds a **second, per-project call** because that is
the only way the CLI exposes service-level data. With 9 registry entries, the sweep's Railway
CLI invocations go from 1 to 10 (1 aggregate + 9 per-project). Sequential, not concurrent — the
sweep runs once daily via launchd; simplicity beats shaving a few seconds off a job with no
deadline.

## The join: project → service, one CLI call away

Services need no new join logic. `apps.yaml` already maps `railway_project_id` to an app name;
passing that same id as `--project` returns that project's own `services` array, each with a
Railway-assigned `id` and `name`. The join is: **for each registry entry, call the CLI with its
`railway_project_id`; every service in the response belongs to that entry's app.** No service
registry, no name-matching, no new `apps.yaml` field.

The response echoes `project.id` — the sweep checks it matches the id requested and drops the
response (logging a warning) if it doesn't, as a defensive sanity check rather than an expected
failure mode.

**All 9 registry entries are swept, active or dormant.** This matches the existing aggregate
lane, which already maps every `railway_project_id` in the registry with no `active` filter
(`railway_cost.infra_rows` has no active check today). Filtering the new lane to `active: true`
only would make the two infra lanes disagree about which projects they cover, for a saving of
5 extra CLI calls a day. Not worth the inconsistency.

## Data model

### A sibling section, not an extension of `infra_usage` — schema v4

`infra_usage` ships **one row per app per capture**, keyed `(period_start, app,
capture_date)`. Service data is **one-to-many under that same key** — each app now has N
service rows per capture. Three ways to fit that in were considered:

1. **Widen `infra_usage` rows to carry a service dimension.** Rejected: every consumer of
   `infra_usage` (`truecost.daily_infra`, `truecost.period_to_date`, the ingest upsert, the v3
   validation contract) is written and tested against one row per `(period_start, app,
   capture_date)`. Widening the key changes the meaning of an existing, shipped, tested
   contract for every existing caller, for a feature that only some readers care about.
2. **A new table with no snapshot section, populated by a separate direct write path.**
   Rejected: it breaks the "local Mac collects, cloud renders" architecture the whole repo
   follows — every other piece of infra data rides the snapshot through `/api/ingest`.
3. **A new snapshot section, sibling to `infra_usage`, own table.** Chosen. Same shape of
   change as `cost_daily` (v2) and `infra_usage` (v3) before it: bump `SCHEMA_VERSION`, add one
   required top-level key, add its own `ITEM_SCHEMAS`/`FIELD_TYPES` entry, add one table, add
   one upsert loop. v1–v3 keep validating exactly as they do today — the outbox can hold
   pre-v4 payloads, a 400 quarantines them as `.rejected`.

`SCHEMA_VERSION = 4`, `SUPPORTED_VERSIONS = (1, 2, 3, 4)`. New required top-level key
`infra_usage_services`, following the same `REQUIRED_KEYS` chaining `shared/snapshot.py`
already uses (`V4_REQUIRED_KEYS = V3_REQUIRED_KEYS + ("infra_usage_services",)`). Mirror the
existing "must not carry a key below its version" guard: v1–v3 payloads carrying
`infra_usage_services` are rejected, exactly like v1 carrying `cost_daily` or v2 carrying
`infra_usage` are today.

### Row shape and primary key

One row per service per capture:

```json
{"capture_date": "2026-08-11", "period_start": "2026-07-27", "app": "b2b-ai-news",
 "service_id": "6cd7456e-...", "service_name": "Postgres",
 "cumulative_usd": 1.860756, "memory_usd": 1.754675, "cpu_usd": 0.078439,
 "egress_usd": 0.0, "volume_usd": 0.027641, "backup_usd": 0.0}
```

Primary key: **`(period_start, app, service_id, capture_date)`** — `service_id` extends the
existing key rather than `service_name`, because Railway service ids are the stable identifier;
a name is user-editable. Stability spot-checked on 2026-08-11: `coach-web` reported service id
`fbd1f9bb-3789-42dd-8e1e-dbda6b997892` both before and after two redeploys that same day, so
the id survives redeployment (the *deployment* id changes; the service id does not). All six dollar fields round to 6 decimals on write, per the existing
repo convention. `backupDollars`/`volumeDollars` arrive as `-0.0` from Railway; **normalize to
`0.0` before rounding** — a negative-zero dollar figure is not meaningful to show and JSON's
`-0.0` is a footgun for any downstream sum or chart.

New table `InfraServiceUsage`, same style as `InfraUsage`:

```python
class InfraServiceUsage(Base):
    __tablename__ = "infra_service_usage"
    period_start: Mapped[str] = mapped_column(String(10), primary_key=True)
    app: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capture_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    service_name: Mapped[str] = mapped_column(String(120))
    cumulative_usd: Mapped[float] = mapped_column(Float, default=0.0)
    memory_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_usd: Mapped[float] = mapped_column(Float, default=0.0)
    egress_usd: Mapped[float] = mapped_column(Float, default=0.0)
    volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    backup_usd: Mapped[float] = mapped_column(Float, default=0.0)
```

Ingest upsert mirrors the existing `infra_usage` loop in `apply_snapshot` exactly — same
get-or-add-else-update shape, keyed on the four-tuple PK instead of the three-tuple.

### Derivation: a sibling function, not a generalized `daily_infra`

`truecost.daily_infra` computes `daily = today − previous within the same (period_start,
app)`, clamping a decrease to `0.0` (a restated-downward or removed-then-readded series is
booked as zero for that capture, never as a negative — and never re-booked as the whole
cumulative, which would double-count everything already captured that period). Service rows
need **the same rule, grouped one level finer**: `(period_start, app, service_id)`.

Rather than add a key-extraction parameter to `daily_infra` — which touches an already-shipped,
already-tested pure function and its call site in `api.py` for a change only the new feature
needs — add a **sibling function `daily_infra_services`** that duplicates the same ~15-line
delta loop with the finer grouping key. This repo already accepts this kind of duplication
(`InfraUsage`/`LlmDaily` are two separate tables with parallel upsert loops rather than one
generalized table); a second small pure function is consistent with that, and `daily_infra`
itself stays untouched and exactly as tested.

**`window_sums` needs no change at all.** It sums `{date: {key: usd}}` over an inclusive
window and is already agnostic to what `key` is — today it's `app`, and `daily_infra_services`
can hand it `{date: {(app, service_id): usd}}` (or `{date: {app: {service_id: usd}}}`, see API
shape below) and it sums correctly with zero edits. Worth a dedicated test to pin this
intentionally rather than by accident.

### API shape: extend `/api/truecost`, not a new endpoint

`GET /api/truecost?days=30` already returns one entry per app with `railway_usd`, `llm_usd`,
`total_usd`, `share`. Add an optional `services` array to each app entry that has any infra
rows in the window, sorted by `total_usd` descending:

```json
{"app": "b2b-ai-news", "display": "B2B AI News", "railway_usd": 12.40, "llm_usd": 3.10,
 "total_usd": 15.50, "share": 0.62,
 "services": [
   {"service": "Postgres", "total_usd": 7.90, "memory_usd": 7.45, "cpu_usd": 0.30,
    "egress_usd": 0.05, "volume_usd": 0.10, "backup_usd": 0.0, "share_of_app": 0.64},
   {"service": "b2b-ai-news", "total_usd": 4.50, "memory_usd": 4.10, "cpu_usd": 0.35,
    "egress_usd": 0.05, "volume_usd": 0.0, "backup_usd": 0.0, "share_of_app": 0.36}]}
```

An app with no service rows for the window (feature not yet swept for it, or a fully failed
per-project call — see Error handling) simply omits `services` or ships `services: []`; the UI
treats both the same. One endpoint, one round trip — consistent with how `/api/truecost`
already joins two lanes in a single response rather than requiring the frontend to stitch two
calls together.

## Error handling

Ten independent CLI calls means ten independent failure points. Each must degrade on its own —
one bad project must not blank out the other nine, and the sweep must still exit 0 no matter
how many fail.

| Condition | Behaviour |
|---|---|
| One project's `--project` call fails (CLI error, non-zero exit, timeout, non-JSON) | That project contributes zero service rows this capture; the other calls proceed independently |
| `project.id` in a per-project response doesn't match the id requested | Row dropped, warning logged — defensive; not an expected case |
| All 10 calls fail (CLI unauthenticated, etc.) | Both `infra_usage` and `infra_usage_services` ship empty; sweep still exits 0 |
| `-0.0` component dollar value | Normalized to `0.0` before rounding, at row-construction time |
| v1–v3 payload carries `infra_usage_services` | 400, quarantined as `.rejected`, exactly like v3-vs-`infra_usage` today |
| New `period_start` seen for a given `(app, service_id)` | First capture's daily = cumulative, same rule as the app-level lane |

**Partial data ships, not none.** If 3 of 9 per-project calls fail, the sweep still ships
service rows for the other 6 — the aggregate lane's top-line numbers are unaffected either way
(it's a separate call), and there's no reason to withhold six projects' worth of drill-down
detail because a seventh timed out. This mirrors the existing "usage lane failing doesn't blank
the infra lane" independence already in `sweep.py`.

The sweep summary line gains a field distinguishing "some projects failed" from "all failed,"
since with 10 calls "partial" is now the likely failure mode rather than the exception:

```
sweep: ... infra=ok infra_services=partial(6/9)
```

## What the UI shows

The Cost page's existing "By app" table stays exactly as it is — app, Railway $, API $, Total.
Each row that has `services` data gets a disclosure toggle. Expanding a row reveals a small
sub-table, same window as the parent (trailing 30 days, no separate date range):

```
▾ B2B AI News                    Railway $12.40   API $3.10   Total $15.50
    Postgres                     $7.90  (64%)   memory $7.45
    b2b-ai-news                  $4.50  (36%)   memory $4.10
```

Two columns beyond service name and dollar total: **share of the app's Railway spend**, and
**memory dollars called out explicitly** (not a fourth column of CPU/egress/volume/backup —
those stay available in the API response for anyone who wants them, but memory is the number
that answers "what should I actually go resize," so it's the only resource surfaced in the
table). This directly answers the motivating question — Postgres is 64% of this app's infra
bill and almost all of that is memory — without adding a chart, a filter, or a new page.

No time series for services. No alerting or threshold badges ("memory is >90% of this
service's cost"). No comparison across apps. The existing per-app table and Cache efficiency
card are unaffected.

## Known limitations

Written with the same candour as the True Cost spec's "Known blind spot" — these are real,
not hedges.

- **No backfill.** Service-level history starts the day this ships. The aggregate lane's
  already-captured `infra_usage` history cannot be retroactively split into services — Railway
  never reported that breakdown for past dates, only current period-to-date.
- **The two lanes can drift from each other by a few cents.** A project's aggregate
  `currentUsageDollars` and the sum of that same project's per-service `totalDollars` come from
  two separate CLI calls, made moments apart, both reading a live period-to-date figure. They
  will not always sum to exactly the same value the top-line table shows. This is expected, not
  a bug — flagging it here so a future "why don't these add up" investigation doesn't start from
  scratch.
- **Ten sequential CLI calls, once a day.** No per-workspace rate limit is documented for
  `railway usage projects --project`, but the volume has now been exercised: 9 sequential
  per-project calls were run back-to-back on 2026-08-11 with zero failures or throttling. That
  is one observation at the exact cadence this feature needs, not a guarantee from Railway —
  a limit could exist above this volume or be introduced later. The per-call degradation in
  Error handling covers it either way.
- **Worst-case sweep runtime grows.** Each call reuses the existing 120s subprocess timeout. If
  every one of the 10 calls hangs to timeout, the infra portion of the sweep alone takes ~20
  minutes. It still degrades correctly (partial or empty data, exit 0) — it just runs long. Not
  worth adding concurrency or a shorter timeout pre-emptively for a job with no deadline; worth
  revisiting if it ever actually happens.
- **A renamed Railway service looks like a new one in the UI**, even though the primary key
  (`service_id`) sees it as continuous. `service_name` is stored per-capture and simply changes
  from one day to the next; no migration/merge logic exists for a rename. Low risk in practice —
  Tom is the only one renaming his own services, and rarely.
- **Sub-micro-cent components round to `0.000000`.** `egressDollars: 1.182e-7` in the sample
  payload rounds to `0.0` at the existing 6-decimal convention. This loses no meaningful
  information (it's a tenth of a hundredth of a cent) but means "egress: $0.00" and "no egress
  measured" are indistinguishable in the stored row. Acceptable; noting it so it isn't mistaken
  for a bug later.
- **Dormant-project calls are pure overhead.** 5 of the 9 registry entries are `active: false`
  and, per the join decision above, still get swept daily. If sweep runtime or CLI load ever
  becomes a real concern, restricting the new lane to `active: true` is the first lever to pull
  — deliberately not pulled now, for consistency with the existing aggregate lane's behavior.

## Out of scope

- Any change to the LLM lane (`/api/usage`, `llm_daily`) — this is infra-only.
- Alerting or threshold badges on memory share.
- Automatic resize/right-sizing suggestions.
- A dedicated service-history page or per-service time series chart.
- A service registry in `apps.yaml` (`by_railway_id` already does all the join work needed;
  services are keyed off the CLI response directly).
- Filtering the new lane to `active: true` projects only (see Known limitations — deliberately
  deferred, not forgotten).
- Anthropic-side service breakdown — not applicable; Anthropic has no service concept.
- Concurrent/parallel CLI calls to shorten worst-case sweep runtime.

## Testing

- **Unit (`railway_cost`):** `fetch_usage(project=...)` appends `--project <id>` to the
  command; per-project call failure returns `None` without raising; `service_rows(payload,
  app, capture_date)` parses, rounds to 6dp, and normalizes `-0.0` → `0.0`; a `project.id`
  mismatch is dropped with a warning; a loop over multiple registry entries where some calls
  fail still returns rows for the ones that succeeded.
- **Unit (`truecost`):** `daily_infra_services` delta and period-rollover behavior, mirroring
  the existing `daily_infra` test cases one grouping level finer; `window_sums` against a
  tuple-keyed daily dict, to pin the "no changes needed" claim above as an actual guarantee.
- **Schema:** v1–v4 all validate; v4 rejects a missing `infra_usage_services`; v3 rejects a
  payload carrying `infra_usage_services`.
- **Web:** ingest upsert dedups on the four-tuple PK across re-shipped captures; `/api/truecost`
  includes `services` only for apps with service rows in the window, sorted descending by
  `total_usd`; `share_of_app` sums to ~1.0 across an app's services.
- **Sweep:** a run where 2 of N per-project calls raise still ships the other N-2 and exits 0;
  the summary line's `infra_services=partial(k/n)` reflects the actual success count.

## Implementation note

No new repo-root file is introduced — `apps.yaml` is unchanged and already in the Dockerfile
`COPY` line, so the "any repo-root file the server reads at boot must be in the Dockerfile"
constraint is satisfied trivially here. The Alembic revision for this feature creates exactly
one table (`infra_service_usage`), cycled upgrade → downgrade → upgrade before merge, same
verification step the True Cost plan used for its two tables. Deploy order is unchanged: merge
→ deploy server → verify → *then* run the local sweep, so v4 payloads are never sent to a
v3-only server.
