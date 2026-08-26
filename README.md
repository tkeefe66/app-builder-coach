# app-builder-coach

A local sweep plus a Railway-hosted dashboard that answers two questions about a
solo developer's side projects: **what am I actually building**, and **what does
it cost to run**.

Nothing here is a service anyone else calls. The sweep reads local git history and
`~/.claude` transcripts on one Mac; the dashboard is a single-user, password-protected
site. Raw git history and prompt content never leave the machine — only derived
counts and classifications are shipped.

**Live:** https://coach-web-production-1f04.up.railway.app

## Read this first

**`docs/HANDOFF.md` is the real entry point** — where things stand, the traps that
have already broken production once, secrets and their locations, and what is left.
This file is only orientation.

## How it fits together

```
Mac (launchd, daily 07:30)                    Railway
  src/sweep.py                                  coach-web
    collect git history          ──┐              POST /api/ingest  → Postgres
    classify with Claude           │  snapshot     background task:
    parse ~/.claude transcripts    ├──  JSON  ──▶    generate a coaching brief
    read `railway usage projects`  │   (v4)          weekly: read the CC changelog
    build profile.md             ──┘              React SPA + read/write API

deployed apps ── POST /api/usage (their own Anthropic token counts) ──▶ coach-web
```

The sweep **always exits 0** and failed ships queue in `data/outbox/` to self-heal,
so a bad network day never costs data.

## Layout

| Path | What |
|---|---|
| `src/` | The local sweep: collector, classifier, usage, railway_cost, profile, shipper |
| `shared/` | Code both sides import — the snapshot schema contract, app registry, pricing |
| `apps/coach_web/` | FastAPI server, SQLAlchemy models, Alembic migrations, React SPA |
| `reporters/` | Canonical per-language usage reporters, copied into other app repos |
| `docs/` | `HANDOFF.md`, plus `superpowers/specs/` and `superpowers/plans/` per phase |
| `apps.yaml`, `taxonomy.yaml`, `rubric.yaml`, `feature-checklist.yaml` | Repo-root config the server reads at boot |

## Commands

```bash
make sweep                                   # the full local pipeline
.venv/bin/python -m pytest -q                # Python tests
cd apps/coach_web/frontend && npx tsc --noEmit && npx vitest run && npm run build
```

No format or typecheck hook is configured — run them yourself before committing.

## Things that will bite you

- **This repo has a git remote, but deploys don't go through it.** `railway up`
  uploads the working directory directly, so `deployment list`'s `meta.commitHash`
  stays null and `origin/main` tells you nothing about what is running. To check
  what is actually deployed, grep the served JS bundle for an API field name.
- **Any repo-root file the server reads at boot must be in the Dockerfile `COPY`
  line.** `apps.yaml` was missed once and crash-looped production;
  `tests/web/test_dockerfile_data_files.py` guards all of them now.
- **Old snapshot versions must never be rejected.** The outbox can hold pre-v4
  payloads and a 400 quarantines them permanently. `shared/snapshot.py` dispatches
  on `schema_version`; every version keeps its original rules.
- **`require_same_origin` must never become global middleware.** `/api/ingest` and
  `/api/usage` are bearer-token clients that send no `Origin`; covering them makes
  the daily sweep queue payloads silently.
- **A `GET` on a POST-only route returns 404 here, not 405** — the SPA fallback
  swallows it. Use `POST` to test whether a route is mounted.
- **Deployment is Railway only.** See `.claude/skills/deploy-coach-web/SKILL.md`.

## Status

All five planned phases are shipped, including database backups (nightly, encrypted,
verified restore). No planned phases remain — what's left is an opportunistic cleanup
list (JS chunk size, login-page shell, empty states, `HEAD` on SPA routes, UTC-vs-local
week skew). See `docs/HANDOFF.md` § "What's actually left".
