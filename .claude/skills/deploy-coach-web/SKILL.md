---
name: deploy-coach-web
description: Use when deploying, redeploying, debugging, or changing configuration (env vars, password, domain, logs) of this repo's coach-web Railway service, or when wiring the local sweep's shipper to it.
---

# Deploying coach-web

Railway project `app-builder-coach` (tkeefe66's Projects), service `coach-web` + Postgres.
Live at https://coach-web-production-1f04.up.railway.app — health: `/api/health`.

**REQUIRED BACKGROUND:** global `railway-cli` skill for command patterns and safety rules.

## Commands

| Action | Command |
|---|---|
| Deploy | `railway up --service coach-web --detach` (from repo root) |
| Status | `railway deployment list --service coach-web --limit 1 --json` → `.status` |
| Logs | `railway logs --service coach-web` (`--build` for build logs) |
| Vars | `railway variables --service coach-web` |

## Two services share one repo — and one `railway.json`

The project has **`coach-web`** (the dashboard) and **`coach-backup`** (a nightly cron job,
`Dockerfile.backup`, `python -m src.backup_nightly`). Both deploy the same uploaded
directory, so **`railway.json` applies to both**, and anything service-specific in it breaks
the other service.

It did, three deploys in a row. `railway.json` used to set
`build.dockerfilePath: "Dockerfile"` and `deploy.healthcheckPath: "/api/health"`, so
`coach-backup` built the *web* image and then failed a healthcheck a cron job can never
answer — 11 attempts, then FAILED, with no backup taken.

**`coach-backup` uses Railway's "Config-as-code file path" service setting**, set on its
settings page to `/railway.backup.json`. That file therefore **must exist in the repo** —
deleting it does not fall back to `railway.json`, it fails the deploy *instantly, before any
build*, with `Deployment does not have an associated build` and nothing else to go on. That
exact failure was produced once by removing the file while the setting still pointed at it.

⚠️ **The setting is not retroactive to a deploy already in flight**, and the three failures
before it was set all read `railway.json` instead. If a `coach-backup` deploy shows a
`/api/health` healthcheck in its logs, the setting is missing or the path is wrong — it is
reading the web config.

**The arrangement that works:** `railway.json` holds only what is true for *every* service
(`builder: DOCKERFILE`), and each service names its own Dockerfile with the
`RAILWAY_DOCKERFILE_PATH` variable — `Dockerfile` on coach-web, `Dockerfile.backup` on
coach-backup. Variables are per-service and *do* apply. Anything else service-specific must
be a service setting in the dashboard, not a repo file.

**Who reads which file:** `coach-web` reads `railway.json`; `coach-backup` reads
`railway.backup.json` via its config-file setting. Because they no longer share a file,
`railway.json` keeps coach-web's `healthcheckPath: /api/health` — the guard that stops a
broken build from replacing a working one.

⚠️ **Keep `dockerfilePath` OUT of `railway.json`.** Each service names its own Dockerfile
through the per-service `RAILWAY_DOCKERFILE_PATH` variable instead. This is deliberate: if
coach-backup ever loses its config-file setting, it falls back to `railway.json`, and the
healthcheck there makes it fail *loudly* on a deploy rather than silently building and
running the wrong image.

`coach-backup` sets `restartPolicyType: NEVER` in its own file, so a failed backup does not
retry in a loop; the next cron tick is the next attempt.

⚠️ The CLI cannot set service-level deploy config at all — `railway environment edit
--service-config` returns "No changes to apply" for every path, on every service, including
`deploy.cronSchedule` and `deploy.startCommand`. Anything not expressible in a config file
or a variable is a dashboard action.

**Running the restore drill is therefore awkward** and worth knowing before you try: the
service's `CMD` runs the backup, and `deploy.startCommand` in `railway.backup.json` was NOT
honoured when tested (the deployment manifest showed `startCommand: null`, and `railway up`
appears to upload the linked main checkout rather than the current worktree). The 2026-08-13
verification was instead done by hand and locally: pull the object from R2, decrypt it, and
`pg_restore` into a throwaway `postgres:18` container. That works and needs no deploy — see
"What's actually left" in `docs/HANDOFF.md` for the measured numbers.

## Backups — and how to actually restore

Nightly at **08:00 UTC** (`0 8 * * *`), the `coach-backup` service runs
`python -m src.backup_nightly`: asserts it is looking at this app's database, `pg_dump
--format=custom`, AES-256-GCM encrypts, uploads to Cloudflare R2 bucket
`coach-web-backups` as `backups/nightly/<YYYY-MM-DD>.dump.enc`, prunes past 30 days.

⚠️ **Railway volume backups and PITR are Pro-plan only and NOT available on this account.**
The R2 dump is the only layer — which is also the only one that survives losing the Railway
project. **Recovery point is the last nightly run; recovery time is seconds.**

⚠️ **`BACKUP_ENCRYPTION_KEY` is the single point of failure.** Without it every object in R2
is unreadable. It must exist somewhere other than Railway.

### Restore procedure (verified 2026-08-13)

Run it **locally** — this needs no deploy, and the in-repo `src/restore_drill.py` has never
been executed against production (the service's `CMD` runs the backup, and
`deploy.startCommand` was not honoured when tried).

Requires local `pg_dump`/`pg_restore` **matching the server major version — currently 18**
(`ghcr.io/railwayapp-templates/postgres-ssl:18`). `pg_restore` older than the server refuses
outright rather than degrading.

```bash
# 1. Credentials come from the service; never paste them into a shell.
railway run --service coach-backup -- .venv/bin/python <<'PY'
# list backups/nightly/, download the newest, split iv(12)||tag(16)||ciphertext,
# AESGCM-decrypt with BACKUP_ENCRYPTION_KEY, confirm it starts with b"PGDMP",
# write the plaintext .dump to disk
PY

# 2. Restore into a throwaway server of the SAME major version
docker run -d --name restore-check -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:18
pg_restore --no-owner --exit-on-error \
  --dbname "postgresql://postgres:x@localhost:55432/postgres" ./backup.dump

# 3. Prove it is really this database, not a plausible-looking archive
psql "postgresql://postgres:x@localhost:55432/postgres" \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"
# feature_units should match the live /api/summary unit_count.

docker rm -f restore-check && rm -f ./backup.dump   # the dump is real user data
```

The 2026-08-13 run: 84,446 bytes plain / 84,474 encrypted (+28 = 12-byte IV + 16-byte tag),
`pg_restore --exit-on-error` exited 0 in ~0.1s, restored `feature_units` 120 matching
production's live `unit_count` of 120.

A scratch database `restore_drill` exists on the Railway Postgres server, created for a
drill that ended up being run locally instead. It is unused — drop it or keep it.

## Topology facts (do not guess these)

- Deploy from **repo root**; never set rootDirectory. The app imports `shared/` and `apps/` from root. `railway.json`'s builder is `DOCKERFILE` (no startCommand) — the multi-stage `Dockerfile` builds the SPA in a node stage, then a python runtime stage serves the API + SPA. Migrations and uvicorn both run via the Dockerfile `CMD` (`alembic -c apps/coach_web/alembic.ini upgrade head && uvicorn apps.coach_web.main:app ...`), in that order.
- Railway vars: `DATABASE_URL` is a **reference** — literally `${{Postgres.DATABASE_URL}}`. `COACH_INGEST_TOKEN`, `COACH_SECRET_KEY`, `COACH_PASSWORD_HASH`, `COACH_USAGE_TOKEN`, `ANTHROPIC_API_KEY` are **literals**.
- **`ANTHROPIC_API_KEY` IS set** (since Phase 4, 2026-08-12) — the server generates a coaching brief on every ingest. It is the *same key* as the one in the local `.env` that the sweep's classifier uses, so **rotating it breaks both**. Optional override: `COACH_BRIEF_MODEL` (defaults to `claude-haiku-4-5`).
- Optional: `COACH_ALLOWED_ORIGINS` (comma-separated). Unset falls back to the request `Host`, which is correct for both current domains — only set it if a new domain needs allowing.
- The app fails fast at startup if any prod secret is missing (`_check_prod_secrets` in `apps/coach_web/main.py`). **`ANTHROPIC_API_KEY` is deliberately NOT in that check** — a missing key surfaces as a `failed` brief on Overview rather than crash-looping the boot.
- Local `.env` needs `COACH_INGEST_URL=https://coach-web-production-1f04.up.railway.app/api/ingest` and `COACH_INGEST_TOKEN` equal to the Railway literal — that pair is how the daily sweep ships snapshots. Failed ships queue in `data/outbox/`.
- `.railwayignore` excludes `.env`, `data/` (personal data), `.venv/`, `docs/`, `.claude/` from uploads. Keep it intact.

## Change the login password

```bash
.venv/bin/python -m apps.coach_web.auth 'NewPassword'
railway variables --service coach-web --set 'COACH_PASSWORD_HASH=<output>'
```

Single-quote the hash — it contains `$`. Redeploy is NOT needed; restart picks it up on next deploy or via dashboard restart.

## Verify after deploy

```bash
A=https://coach-web-production-1f04.up.railway.app
curl -s $A/api/health
curl -s -X POST $A/api/login -H 'Content-Type: application/json' -d '{"password":"..."}' -c /tmp/c -o /dev/null -w '%{http_code}'
curl -s $A/api/summary -b /tmp/c
# All 5 security headers must be present (they were 0 before Phase 5):
curl -sI $A/ | grep -icE 'x-frame|content-security|strict-transport|x-content-type|referrer-policy'
```

Login is rate-limited: 5 attempts/60s per process, then 429.

**Then run `make sweep`.** It is the deploy check that matters most: the sweep sends
**no `Origin` header**, so if `require_same_origin` ever leaks onto the ingest or usage
routers this is the only place it shows up — and it shows up silently, as
`queued=1` instead of `shipped=1`, not as an error.

`/docs`, `/redoc`, `/openapi.json` return the SPA (200), not FastAPI docs — `docs_url=None`
is set. A 200 there is not a finding; check the body before reporting one.
