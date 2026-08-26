# Remaining work — design

> **⚠️ SUPERSEDED IN ONE PLACE — read before following the backups section.** This spec's
> backup design (Railway volume snapshots + point-in-time recovery) turned out to be
> **impossible on this account: both are Pro-plan only.** What was actually built instead is
> a nightly `coach-backup` cron service — encrypted `pg_dump` → Cloudflare R2, ported from
> the sibling `family-tree` repo — which is the layer that survives losing the Railway
> project anyway. Everything else in this spec shipped as designed. Current state lives in
> `docs/HANDOFF.md`; operational detail in the `deploy-coach-web` skill.

**Date:** 2026-08-12
**Status:** implemented 2026-08-13, with the backups section superseded as noted above
**Supersedes:** the "What's actually left" list in `docs/HANDOFF.md`, which this spec
resolves item by item. Update that list as each item lands; do not maintain two backlogs.

## Why now

All five build phases are shipped and live, and the last real defect — the change
fingerprint including trailing spend, which defeated the brief suppression gate on every
ingest — was fixed and **verified in production on 2026-08-12**: the 17:56:27Z ingest
produced no brief, the first confirmed suppression. What remains is not feature work. It
is one genuine risk (no database backups), two pieces of small hardening, and one
unfinished rollout.

This spec covers all of it so the leftovers stop being a list nobody has decided about.

## Non-goals

- **The deferred-minors list in HANDOFF.md stays deferred.** A whole-branch review
  triaged each entry and ruled *defer*, with reasons. Working through them converts a
  deliberate decision into work.
- **No schema migrations.** Nothing here changes the data model. That is a deliberate
  property of the sequence, not a coincidence: it means no item in this plan can put the
  database in a state the backups don't yet cover.
- **No refactor of the 137 inline `style={{}}` usages.** See item 2.

---

## Item 0 — Clear the decks

Small, and it removes noise that makes the repo look mid-flight when it isn't.

1. Fast-forward `worktree-handoff-fingerprint-doc-fix` into `main` and push. It carries a
   documentation-only correction — the assessment-loop section still described the
   fingerprint as six components including spend, which is the design that was just
   removed for defeating the gate — **and this spec**, which was written on the same
   branch. Merging it is what puts this document on `main`.
2. Delete the three branches already fully merged into `main`: `phase4-coach`,
   `phase5-interactive`, `infra-services` (local and remote).

**Done when:** `git branch --merged main` lists only `main`, and `main` is level with
`origin/main`.

---

## Item 1 — Database backups

The only genuine risk on the list. Six tables hold data that exists nowhere else —
`goals`, `notes`, `dismissals`, `feature_checkoffs`, `brief_recommendations`, and
`briefs`. Everything else can be rebuilt by re-running the sweep. A bad migration is
currently permanent.

Railway has shipped native Postgres backup features since this repo's handoff was
written, so this is now mostly configuration plus one drill — not a service to build.

### Design

Three layers exist; this item takes the first two, plus a manual instance of the third.

| Layer | Enabled how | Protects against |
|---|---|---|
| Volume backups | Backups tab, daily + weekly schedules | Bad deploys, data mistakes |
| Point-in-time recovery | Backups tab, one click | A bad migration or `DROP TABLE` between snapshots |
| Logical dump (`pg_dump`) | Run by hand during the drill | Loss of the project or volume itself |

**Enable PITR first, and before anything else in this spec.** Two properties force the
ordering:

- The restore window begins at the **first post-enable base backup**. Enabling it today
  buys nothing retroactively, so every day it is not enabled is a day that can never be
  recovered to.
- Enabling it **redeploys the Postgres service**, so the dashboard blips. Better to spend
  that blip now than during an incident.

Volume backup schedules: **daily** (kept 6 days) and **weekly** (kept 1 month). Monthly
is not worth the storage here — data older than a month is reconstructible from the
sweep for everything except the six app-owned tables, and PITR covers the recent window
where real mistakes happen.

**A limit that must be written down, because it is counterintuitive:** wiping a volume
deletes all of its backups. Volume snapshots and PITR protect against mistakes *in* the
database, not against losing the volume or the project. The logical dump is the only
layer that survives that, which is why the drill's dump is kept rather than discarded.

### The restore drill

A backup that has never been restored is unverified. Run the drill once as part of this
item, not as a follow-up.

1. `railway connect postgres --tunnel-only` (the public proxy is deliberately not
   enabled on this project — `DATABASE_PUBLIC_URL` is an unresolved template, confirmed
   in the Phase 5 security baseline).
2. `pg_dump --format=custom --no-owner` to `data/backups/`. That directory is excluded
   from Railway uploads by `.railwayignore` and from git by `.gitignore`'s `data/*`
   (both confirmed 2026-08-12) — which matters, because the dump contains real data.
3. Create a scratch `restore_drill` database, `pg_restore --exit-on-error` into it.
4. Compare row counts for all six app-owned tables against production.
5. Drop the scratch database.

Requires `pg_dump`/`pg_restore`/`psql` locally (`brew install libpq` if absent) — check
this before starting, not halfway through.

### What gets recorded

Two numbers come out of the drill and belong in `docs/HANDOFF.md` and the
`deploy-coach-web` skill: **how long the restore took** and **how old the dump was**.
Those are the real recovery time and recovery point. Without them "we have backups" is
still an assumption.

**Done when:** both schedules are on, PITR reports a healthy first base backup, the drill
has completed with matching row counts, and both numbers are documented.

---

## Item 2 — Tighten the CSP

Current header is `frame-ancestors 'none'` only. HANDOFF.md frames the full CSP as
blocked behind moving the SPA's inline styles to classes. That framing is wrong, and
correcting it is part of this item.

### Design

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; connect-src 'self'; object-src 'none';
base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

`apps/coach_web/frontend/index.html` loads nothing external — a same-origin favicon and
the bundled module, nothing more — so `default-src 'self'` is safe as it stands.

**`style-src 'unsafe-inline'` is permanent, not a concession.** Recharts injects inline
styles at runtime, so moving all 137 `style={{}}` usages to classes would not let the
directive be tightened. The refactor buys nothing for CSP, which is why it is a non-goal.
This is the correction HANDOFF.md needs: the full CSP was never actually blocked on that
work.

Everything else in the header is real: `script-src 'self'` blocks injected script,
`base-uri` blocks base-tag hijacking, `form-action` blocks form exfiltration, and
`object-src 'none'` closes the plugin vector.

### Verification

A green test does not prove the dashboard still renders. The risk in this item is a
missed directive silently blanking a page, so verification is **both**:

- A test pinning each directive, mutation-checked: remove `script-src` and confirm it
  goes red.
- A live browser walk of all six pages — Overview, Capabilities, Activity, Cost,
  Adoption, Goals & Coach — watching the console for CSP violations, including the
  interactive paths (create a goal, dismiss an item) since those are the write routes.

**Done when:** the header is live, the test is mutation-verified, all six pages render
clean with no console violations, and HANDOFF.md's claim about the refactor is corrected.

---

## Item 3 — Make the drift check show its own staleness

`COACH_CONSOLE_FROM/_TO/_SPEND` pin the Anthropic Console figure for a fixed window. The
gap between that and tracked spend *is* the measured blind spot, which is the point — but
left alone, the anchor ages and the "Untracked spend" tile quietly stops meaning
anything. Nothing in the system currently says so.

### Design

The tile already renders the window (`vs Console {from}→{to}` in
`apps/coach_web/frontend/src/pages/Cost.tsx`). What is missing is any signal that the
window is old. This is additive on both sides — no new endpoint, no schema change.

**API** (`apps/coach_web/api.py`, the `drift` object in the truecost response): add

- `age_days: int` — days from `console_to` to today
- `stale: bool` — `age_days > 35`

35 days, not 30: the anchor is re-set from a monthly Console figure, so a 30-day
threshold would flag as stale during the normal window between one month's reading and
the next. 35 gives that a few days of slack while still catching a genuinely forgotten
anchor.

`drift` stays `null` when the variables are unset or invalid — the existing validation
and its warning logs are unchanged.

**UI** (`Cost.tsx`, `StatTile.tsx`): when `stale` is true, the tile shows the age and
reads visibly as needing attention. `StatTile` currently takes `label`/`value`/`sub`/`dim`;
this adds one state to it rather than special-casing the Cost page, so any future tile
can use it.

### Tests

- pytest: age math, and the boundary in both directions — 35 days is not stale, 36 is.
  Assert the asymmetric case, since an inequality assertion cannot pin an operator.
- vitest: renders the anchor date; renders the stale treatment only when `stale` is true.
- Mutation-check: flip `>` to `>=` and confirm the boundary test goes red.

**Done when:** a deliberately old anchor visibly flags on the live Cost page.

---

## Item 4 — Parental-Stories usage reporter — DROPPED

> **⚠️ Not built. The application is being retired (Tom, 2026-08-13).** The reporter was
> written and reviewed but never deployed; `parental-stories` was removed from `apps.yaml`
> instead. This section is kept for the reasoning it records — chokepoint discovery, the
> `tsc`/`dist` trap, streaming under-report — which applies to any future reporter rollout.

The last app not reporting Anthropic spend. It is currently a known omission sitting
inside the drift figure, which weakens the drift number: the gap should be *unknown*
spend, not one app we chose not to instrument.

Different repo. `docs/reporter-rollout-prompt.md` covers the procedure; the notes below
are the traps this rollout has already hit elsewhere.

### Design

1. **Service verified 2026-08-12 — this step is already done.** Project
   `32d8d935-8144-48fd-8b8b-db1359f5532c` holds three services (`Front End`, `Postgres`,
   `Back End`); only **`Back End`** carries `ANTHROPIC_API_KEY`, and it carries neither
   `COACH_USAGE_URL` nor `COACH_USAGE_TOKEN`. Verified with
   `railway variables list -p 32d8d935-8144-48fd-8b8b-db1359f5532c -e production -s "Back End" --kv`.
   Re-verify per service rather than trusting a table if this is picked up much later —
   the service table in HANDOFF.md has been wrong once (Purchase-Inventory was listed as
   two services and is actually four), and a missed service is silently unreported spend.
2. **Find every call site, then look for a chokepoint.** Grep for `messages.create`,
   `messages.stream`, **and** `new Anthropic(`. A repo may hold several independent
   clients (b2b-ai-news-source had four across three files), or all of them may funnel
   through one wrapper (purchase-inventory: 23 matching files, one edit). Determine which
   before editing anything.
3. **Wire the reporter.** It never raises and never blocks. If the repo is TypeScript
   with `allowJs: false`, two traps apply: the copied `usage.js` is dropped from `dist/`
   and the service crash-loops at boot *after a green typecheck and a green build*, so
   the build script needs an explicit `cp`; and it needs a `usage.d.ts` beside the copy.
   Do not port the reporter to TypeScript — that forks the one source of truth.
4. Set `COACH_USAGE_URL` and `COACH_USAGE_TOKEN`, deploy, exercise a real call.

**Known limitation, worth stating rather than discovering:** streaming calls report only
after the stream drains, so a client that disconnects mid-response leaves that call
unreported. It surfaces as drift.

**Done when:** a `parental-stories` row appears in `llm_daily`, the HANDOFF.md service
table is updated, and `docs/reporter-rollout-prompt.md` is deleted — the rollout it
describes is then complete and the file becomes a decoy.

---

## Sequencing

```
Item 0 (decks)
   └─> Item 1 (backups)  ← everything else lands after the database is protected
          ├─> Item 2 (CSP)
          ├─> Item 3 (drift staleness)
          └─> Item 4 (reporter, different repo — independent, can run any time)
```

Items 2, 3 and 4 are mutually independent. Item 1 gates them not by technical dependency
but by policy: no changes to the deployed app before the database it writes to can be
restored.

## Testing strategy

Per the process this repo has already paid to learn: **a green suite is not evidence.**
Five separate times during the assessment loop a reviewer deleted a load-bearing guard
and the entire suite stayed green.

Every new guard in this spec is mutation-checked — break it deliberately, confirm the
suite goes red — and the two named corollaries apply directly here:

- An inequality assertion is symmetric and cannot pin an operator, which is why item 3
  tests the stale boundary from both sides.
- Fixtures must span production magnitude, which is why item 3's age fixtures use real
  anchor ages rather than a token 1 and 2 days.

The two items whose failure modes tests cannot reach — backups and CSP — get physical
verification instead: a restore drill and a browser walk.

## Risks

| Risk | Mitigation |
|---|---|
| Enabling PITR redeploys Postgres; dashboard blips | Do it first, deliberately, not during an incident |
| A missed CSP directive blanks a page | Browser-walk all six pages plus the write paths, not just tests |
| Parental-Stories `tsc`/`dist` trap crash-loops the service | Verify `dist/` contains the reporter before deploying |
| Drift threshold flags during the normal monthly window | 35 days, not 30 |
| The deferred-minors list gets picked up as tickets | Stated as a non-goal here and in HANDOFF.md |

## Definition of done

`docs/HANDOFF.md`'s "What's actually left" is reduced to accepted limitations only —
`/api/reassess`'s synchronous Sonnet call, the stateless session cookie, deploys not
going through git, and unmodelled `opus-5[1m]` premium pricing — each of which is a
recorded decision rather than pending work.
