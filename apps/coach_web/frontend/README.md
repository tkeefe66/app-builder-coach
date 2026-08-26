# coach-web frontend

React + TypeScript + Vite SPA for the coach-web dashboard. Built in the
Dockerfile's node stage and served by FastAPI with an SPA fallback — it is not
deployed independently.

## Commands

Run from this directory:

```bash
npm install
npm run dev        # local dev server; expects the API on the same origin
npx tsc --noEmit   # typecheck (no format/typecheck hook is configured — run it yourself)
npx vitest run     # tests
npm run build      # what the Dockerfile runs
```

`npx tsc --noEmit && npx vitest run && npm run build` is the gate before any commit
that touches this directory.

## Layout

| Path | What |
|---|---|
| `src/pages/` | One component per route: Overview, Capabilities, Activity, Cost, Adoption, Goals |
| `src/components/` | Shared UI — `StatTile`, `StatusChip`, `GradeCard`, `BriefCard`, `WeeklyBars`, `Empty` |
| `src/api.ts` | `get` / `post` / `patch` / `del`. All 401s redirect to `/login` |
| `src/tokens.css` | Colour tokens; charts read them via `cssVar()` |
| `src/__tests__/` | Vitest + Testing Library |

## Conventions worth knowing

- **Writes go through `post`/`patch`/`del` in `src/api.ts`**, never a bare `fetch`.
  The browser attaches `Origin` automatically and the server's `require_same_origin`
  dependency checks it; a hand-rolled `fetch` that omits `credentials: "same-origin"`
  will 401.
- **Inline `style={{}}` is used throughout.** That is why the app's
  Content-Security-Policy is `frame-ancestors 'none'` only — a `style-src` directive
  would break every page. Moving these to classes is the prerequisite for a fuller CSP.
- **Tests stub `fetch` per URL, not globally.** A stub returning one body for every
  request will feed the wrong payload to a page that loads several endpoints — that
  bug has already been shipped once. See `src/__tests__/Writes.test.tsx` for the shape.
- Charts follow the dataviz token palette in `tokens.css`; `cssVar()` reads colours at
  render time, so an OS theme flip mid-session leaves marks stale until re-render.

## Known gaps

- Single JS chunk (~614 kB). Code-split when the app grows.
- The login page renders inside the app shell, so logged-out users see nav links.
- Some lists lack empty states (Capabilities, parts of Overview).
