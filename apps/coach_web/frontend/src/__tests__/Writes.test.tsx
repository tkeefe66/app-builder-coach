import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Goals from "../pages/Goals";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function routes(overrides: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = {
    "/api/briefs": { assessment: null, deltas: [], history: [], recurring: [] },
    "/api/goals": { goals: [{ id: 1, kind: "tag", target: "auth",
      title: "Ship auth", target_date: "2026-09-01", status: "active",
      created_at: "2026-08-12T07:00:00+00:00" }] },
    "/api/dismissals": { dismissals: [{ id: 7, kind: "tag", target: "auth",
      reason: "not now", created_at: "2026-08-12T07:00:00+00:00" }] },
    "/api/overview": { never_built: ["hooks"], stale: [], adoption_gaps: [],
      active_goals: [] },
    "/api/notes": { notes: [] },
    ...overrides,
  };
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(table).find((k) => url.startsWith(k));
    return Promise.resolve(json(key ? table[key] : { status: "ok" }));
  });
}

describe("Goals & Coach writes", () => {
  it("lists active goals and dismissals", async () => {
    vi.stubGlobal("fetch", routes());
    render(<Goals />);
    expect(await screen.findByText("Ship auth")).toBeInTheDocument();
    // Match on the reason, not /auth/ -- "Ship auth" contains "auth" and a
    // loose regex would match two elements and throw.
    expect(await screen.findByText(/not now/)).toBeInTheDocument();
  });

  it("creates a goal from a gap", async () => {
    // Free text is gone by design -- goals come from GoalPicker's gap list
    // (see AssessmentCard for the other path, off a recommendation).
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<Goals />);
    await screen.findByText("Ship auth");
    fireEvent.click(await screen.findByRole("button", { name: "Add hooks" }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/goals" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).target).toBe("hooks");
    });
  });

  it("un-dismisses", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<Goals />);
    await screen.findByText(/not now/);
    fireEvent.click(screen.getByRole("button", { name: /un-dismiss/i }));
    await waitFor(() => {
      const deleted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/dismissals/7" && i?.method === "DELETE");
      expect(deleted).toBeTruthy();
    });
  });
});

import Adoption from "../pages/Adoption";

describe("Adoption writes", () => {
  function board() {
    return vi.fn((input: RequestInfo | URL, init?: any) => {
      const url = String(input);
      if (url.startsWith("/api/adoption/board") && (!init || !init.method)) {
        return Promise.resolve(json({ features: [{
          name: "plan mode", lesson: "09", source: "checklist",
          discovered_at: "2026-01-01", status: "never-touched",
          detected_status: "never-touched", checked_off: false,
          last_used: null, history: [] }] }));
      }
      return Promise.resolve(json({ status: "ok" }));
    });
  }

  it("checks a feature off", async () => {
    const fetchMock = board();
    vi.stubGlobal("fetch", fetchMock);
    render(<Adoption />);
    await screen.findByText("plan mode");
    fireEvent.click(screen.getByRole("button", { name: /check off/i }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/checkoffs" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).feature_name).toBe("plan mode");
    });
  });

  it("dismisses a feature", async () => {
    const fetchMock = board();
    vi.stubGlobal("fetch", fetchMock);
    render(<Adoption />);
    await screen.findByText("plan mode");
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/dismissals" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).kind).toBe("feature");
    });
  });
});

import Overview from "../pages/Overview";

describe("Overview goals", () => {
  it("shows active goals", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/briefs")) {
        return Promise.resolve(json({ assessment: null, deltas: [], history: [],
          recurring: [] }));
      }
      return Promise.resolve(json({
        freshness: { captured_at: "2026-08-12T07:00:00+00:00", received_at: null },
        tiles: { units_this_week: 1, commits_this_week: 2, streak_days: 3,
          streak_last_active: "2026-08-12", sessions_this_week: 4,
          cost_this_week: 1.5 },
        never_built: [], stale: [], adoption_gaps: [],
        active_goals: [{ id: 1, kind: "tag", target: "auth",
          title: "Ship auth", target_date: "2026-09-01" }],
        // Shape copied from GradeCard.test.tsx, not invented.
        grade: { level: "junior", level_label: "Junior Engineer",
          next_level: "mid", next_label: "Mid-Level Engineer",
          percent_to_next: 72, gaps: [] },
      }));
    }));
    render(<Overview />);
    expect(await screen.findByText("Ship auth")).toBeInTheDocument();
  });
});
