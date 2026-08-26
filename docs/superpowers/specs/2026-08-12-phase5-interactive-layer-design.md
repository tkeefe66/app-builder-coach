# Phase 5 design: interactive layer + write-path hardening

**Status:** approved design, not yet planned or built.
Fills in the Phase 5 bullet of
`docs/superpowers/specs/2026-08-02-coach-web-dashboard-design.md` (§Data model
"App-owned", §Backend write endpoints) and closes the security findings that
`docs/HANDOFF.md` deferred *to* Phase 5.

## Why

Every table in the app today is sweep-owned: the dashboard reports what the
collector found and the coach reads it back. Nothing Tom types is stored. That
makes three things impossible:

1. **He cannot wave a suggestion off.** The brief re-suggests the same gaps
   forever, because `build_context` has no notion of "I've considered that."
2. **He cannot record a capability the detector cannot see.** The Adoption board
   says `never-touched` for anything the sweep does not observe, with no way to
   say "I learned this."
3. **He cannot record intent.** There are no goals, so "what am I working
   toward" lives outside the tool the tool exists to answer.

This is also the phase where the app gets its first **write** endpoints, which
is the moment several deferred security items stop being theoretical.

## Security baseline (measured 2026-08-12 against the live app)

Verified against the deployed service, not read from source:

| Check | Result |
|---|---|
| FastAPI auto-docs exposed | **No** — `/docs`, `/openapi.json` return the SPA fallback; `docs_url=None` is working |
| Login throttle under 40-way concurrency | **Holds** — exactly 5 got 401, 35 got 429 |
| Public Postgres proxy | **None** — `DATABASE_PUBLIC_URL` is an unresolved template; no `RAILWAY_TCP_PROXY_*` provisioned |
| Security headers | **Zero present** |
| Logout route | **Does not exist** |

So the two real gaps are **headers** and **logout**, plus the CSRF question
below. The `HANDOFF.md` claim that Postgres has no public proxy is correct.

## Decisions taken (owner, 2026-08-12)

| Question | Decision |
|---|---|
| Scope | **All of Phase 5 in one spec.** Decomposition into sub-projects was offered and declined. |
| CSRF | **Origin/Referer check on cookie-authenticated writes.** |
| Logout | **Clear the cookie.** No server-side session store. |
| Dismissal filtering | Filter **all three** brief lists (`never_built`, `stale`, `adoption_gaps`); Overview keeps showing everything. |
| Notes subjects | **All three** — tag, feature, brief. |

## Write-path hardening

### Origin check

`SameSite=Lax` already blocks classic cross-site CSRF: browsers do not attach
the cookie to a cross-site `POST`. The residual gap is that SameSite is scoped
to the registrable domain (`tomkeefe.ai`), and other apps run under that domain —
a compromised sibling subdomain is **same-site** and would carry the session
cookie on a write.

A `require_same_origin` dependency on every cookie-authenticated write closes
that. Browsers send `Origin` on all `POST`/`PATCH`/`DELETE`, including
same-origin, so requiring it to match is safe.

**Trap 1 — safe methods.** Browsers send `Origin` on `POST`/`PATCH`/`DELETE`, but
**not** on same-origin `GET`. A dependency that demanded `Origin` unconditionally
would 403 every read endpoint and blank the entire dashboard. The dependency
therefore inspects `request.method` and enforces **only on unsafe methods**,
returning immediately for `GET`/`HEAD`/`OPTIONS`. That makes it safe to attach at
router level.

**Trap 2 — machine clients.** `/api/ingest` and `/api/usage` are the sweep and the
reporters. They authenticate with bearer tokens and send **no `Origin` header at
all**. Applying this as blanket middleware on all writes would break the daily
sweep the first time it ran. It is therefore a **router-level dependency on the
cookie-authenticated router only**, never global middleware.

`POST /api/login` is exempt: it carries no session cookie, so there is nothing for
a sibling subdomain to ride, and login-CSRF (forcing a victim into the attacker's
session) is meaningless in a single-user app. `POST /api/logout` **is** covered —
a forced logout is a real, if minor, nuisance attack.

Allowed origins come from `COACH_ALLOWED_ORIGINS` (comma-separated). When unset,
the check falls back to the request's own `Host` — correct for both Railway
domains without configuration, and still rejects a sibling subdomain.

A request with **no** `Origin` and **no** `Referer` on a cookie-authenticated
write is rejected. That is the safe default: every browser sends one.

### Security headers

Applied by middleware to every response:

| Header | Value |
|---|---|
| `X-Frame-Options` | `DENY` |
| `Content-Security-Policy` | `frame-ancestors 'none'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |

**Deliberately a minimal CSP.** The SPA uses React inline `style={{...}}`
throughout, so a `style-src` directive would break the entire UI. Framing
protection is the real exposure — anyone can currently iframe the login page and
overlay a fake one. A full CSP is separate work and needs the inline styles
moved to classes first; it is out of scope here rather than forgotten.

Middleware does not cover unhandled 500s. Accepted: the SPA and API return
handled responses, and a 500 body carries no framing risk.

### Logout

`POST /api/logout` deletes the cookie and returns 200. **POST, not GET** — a
state-changing GET is reachable from a plain link, and `SameSite=Lax` *does*
attach the cookie to top-level navigations.

**Accepted limitation, stated plainly:** the session is a stateless signed
cookie, so logout clears the client's copy and nothing else. A copied cookie
stays valid until its 30-day expiry. Server-side revocation (a session-version
value the signer includes) was offered and declined as disproportionate for a
single-user dashboard. If the threat model ever changes, that is the upgrade.

### Rate-limiter lock

`LoginRateLimiter.check()` is a read-modify-write on a list with no lock. Tested
at 40-way concurrency it behaved correctly (5 through), so this is **not** a
live vulnerability — but the race is real in principle. A `threading.Lock`
around the check→append sequence removes it for three lines. The existing
comment about single-instance scope stays accurate and stays put.

## Data model

Four app-owned tables. **Never written by ingest** — that separation is the
point of the "app-owned" grouping in the original spec.

```python
class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))        # "tag" | "feature"
    target: Mapped[str] = mapped_column(String(120))     # tag name or feature name
    title: Mapped[str] = mapped_column(String(200))
    target_date: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|done|abandoned
    created_at: Mapped[str] = mapped_column(String(32))


class FeatureCheckoff(Base):
    __tablename__ = "feature_checkoffs"
    feature_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    checked_at: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), default="")


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(16))   # "tag" | "feature" | "brief"
    subject_id: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))


class Dismissal(Base):
    __tablename__ = "dismissals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))        # "tag" | "feature"
    target: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[str] = mapped_column(String(32))
```

`(kind, target)` on `dismissals` and `(subject_kind, subject_id)` on `notes` are
indexed, not unique — dismissing twice is idempotent at the API layer, and a
subject can carry many notes.

## The integrations (why this is not just CRUD)

Four tables of pure CRUD would be busywork. The value is that three of them
change behaviour the app already has:

### Dismissals feed the brief

`brief.build_context` filters dismissed items out of **all three** lists it
sends to the model:

- `never_built` and `stale` drop entries with a `kind="tag"` dismissal
- `adoption_gaps` drops entries with a `kind="feature"` dismissal

The coach stops re-suggesting things already considered and rejected. This is
the entire reason `dismissals` is in the original spec.

**Overview keeps showing everything.** Dismissing changes what the *coach*
says, not what the *data* says — a dismissed gap is still a gap, and hiding it
would make the dismissal irreversible-by-forgetting. There is no un-dismiss UI
in this pass; `DELETE /api/dismissals/{id}` exists and the list is visible on
Goals & Coach.

### Check-offs override the Adoption board

`/api/adoption/board` gains a `checked_off` boolean per feature. A feature with
a check-off row reports `status: "checked-off"` regardless of what the sweep
detected, with the detected status preserved as `detected_status` so nothing is
lost. Manual knowledge beats a detector that cannot see it.

### Goals surface on Overview

`/api/overview` gains an `active_goals` array. Per the original spec, streaks
and weekly targets are **computed at read time** from `activity_daily` + `goals` —
no stored counters. A goal carries no progress column; progress is derived.

Notes have no behavioural integration — they are annotation, displayed beside
their subject.

## API

All under the existing authenticated router (`Depends(require_user)`), all
writes additionally behind `Depends(require_same_origin)`.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/logout` | Auth router; clears the cookie |
| `GET`/`POST` | `/api/goals` | List / create |
| `PATCH`/`DELETE` | `/api/goals/{id}` | Update status, title, target_date / delete |
| `GET`/`POST` | `/api/notes` | List (filter by `subject_kind`+`subject_id`) / create |
| `DELETE` | `/api/notes/{id}` | |
| `GET`/`POST` | `/api/dismissals` | List / create (idempotent on `(kind, target)`) |
| `DELETE` | `/api/dismissals/{id}` | Un-dismiss |
| `POST` | `/api/checkoffs` | Create by `feature_name` (idempotent) |
| `DELETE` | `/api/checkoffs/{feature_name}` | Un-check |

Request bodies are Pydantic models with explicit `max_length` matching the
column widths, so an over-long body is a 422 rather than a database error or a
silent truncation. `kind` / `subject_kind` / `status` are validated against
their allowed sets.

## Frontend

- **Goals & Coach** — goal list with create/edit/complete, the dismissals list
  with un-dismiss, and notes attached to briefs.
- **Adoption** — a check-off control per feature and a dismiss control per
  feature; checked-off features render distinctly from detected ones.
- **Capabilities** — a dismiss control per tag, and tag notes.
- **Overview** — active goals beside the existing tiles.

Writes go through a shared `post`/`del` helper in `src/api.ts` alongside the
existing `get`, so the `Origin` behaviour and error handling live in one place.

## Error handling

| Failure | Behavior |
|---|---|
| Write with missing/mismatched `Origin` | 403 with a clear message; logged |
| Write without a session | 401 (existing `require_user`) |
| Over-long or malformed body | 422 from Pydantic, no partial write |
| Duplicate dismissal / check-off | 200, idempotent — no error, no duplicate row |
| `DELETE` of a nonexistent id | 404 |
| Machine clients (`/api/ingest`, `/api/usage`) | Unaffected — bearer-token routers, no Origin dependency |

## Testing

- **Origin check:** matching origin passes; foreign origin 403; **sibling
  subdomain 403** (the case this control exists for); missing Origin+Referer 403;
  `COACH_ALLOWED_ORIGINS` honoured; **`GET` with no Origin still returns 200**
  (browsers omit it on same-origin reads — without this the dashboard blanks);
  **`/api/ingest` and `/api/usage` still work with no Origin header** (the
  regression that would break the sweep).
- **Headers:** every listed header present on an API response and on an SPA response.
- **Logout:** clears the cookie; a subsequent authenticated GET is 401; it is
  `POST`-only (a `GET` must not clear it).
- **Rate limiter:** existing tests stay green with the lock added.
- **Per entity:** create/list/update/delete; validation rejects bad `kind` and
  over-long bodies; idempotency on dismissals and check-offs; 404 on missing id.
- **Integrations:** a tag dismissal removes it from `build_context`'s
  `never_built` and `stale`; a feature dismissal removes it from
  `adoption_gaps`; a check-off flips the board's `status` to `checked-off` while
  `detected_status` keeps the sweep's value; `/api/overview` carries
  `active_goals`.
- **Alembic:** revision inspected (only the four `create_table`s plus indexes)
  and cycled up/down/up.

## Out of scope

- Server-side session store / cookie revocation (offered, declined; documented above).
- A full Content-Security-Policy beyond `frame-ancestors` — needs inline styles moved to classes first.
- Per-IP login throttling; the global window is correct for a single-user app.
- Multi-user, sharing, or any notion of authorship on the new rows.
- Editing note bodies (create + delete only); editing a brief.
- Reminders or notifications on goal target dates.
- Database backups — a real gap, but infrastructure work, not this feature.

## Deploy note

One optional new environment variable, `COACH_ALLOWED_ORIGINS`; unset falls back
to the request `Host`, which is correct for both current domains. No new
repo-root file, so the Dockerfile `COPY` line needs no change — but do not
remove anything from it. The migration creates four tables and runs
automatically via the Dockerfile `CMD`.
