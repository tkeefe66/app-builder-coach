import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Goals from "../pages/Goals";

afterEach(() => vi.restoreAllMocks());

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}

const briefs = {
  assessment: {
    created_at: "2026-08-11T07:00:00+00:00", day: "2026-08-11",
    kind: "assessment", status: "ok", stale: false, error: "",
    summary: "You ship fast and deploy by hand.",
    recommendations: [{ id: 1, title: "Containerize purchase-inventory",
      kind: "tag", target: "deploy-docker", why: "Foundational.",
      evidence: "14 units, zero containers.", outcome: "open" }],
  },
  deltas: [],
  history: [],
  recurring: [],
};

const overview = {
  never_built: ["websockets-sse"], stale: [],
  adoption_gaps: ["background tasks"], active_goals: [],
};

// Route by URL: a stub returning one body for every path would feed the briefs
// payload to the goals loader. `calls` records every request (GET included)
// so callers can count how many times a given endpoint was hit.
function stub(posts: string[] = [], calls: string[] = []) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(url);
    if (init?.method && init.method !== "GET") posts.push(`${init.method} ${url}`);
    if (url.startsWith("/api/briefs")) return Promise.resolve(jsonResponse(briefs));
    if (url.startsWith("/api/overview")) return Promise.resolve(jsonResponse(overview));
    if (url.startsWith("/api/goals")) return Promise.resolve(jsonResponse({ goals: [] }));
    if (url.startsWith("/api/dismissals")) {
      return Promise.resolve(jsonResponse({ dismissals: [] }));
    }
    return Promise.resolve(jsonResponse({ status: "ok" }));
  }));
}

const briefsCallCount = (calls: string[]) =>
  calls.filter((u) => u.startsWith("/api/briefs")).length;

describe("Goals page", () => {
  it("leads with the assessment, not the archive", async () => {
    stub();
    render(<Goals />);
    expect(await screen.findByText("You ship fast and deploy by hand."))
      .toBeInTheDocument();
    expect(screen.getByText("Containerize purchase-inventory")).toBeInTheDocument();
  });

  it("adds a goal from a recommendation with its target prefilled", async () => {
    const posts: string[] = [];
    const calls: string[] = [];
    stub(posts, calls);
    render(<Goals />);
    await screen.findByText("You ship fast and deploy by hand.");
    const before = briefsCallCount(calls);
    await userEvent.click(await screen.findByRole("button", { name: "Add as goal" }));
    await waitFor(() => expect(posts).toContain("POST /api/goals"));
    // The write also converts matching recommendations as a side effect, so
    // /api/briefs must be reloaded, not just /api/goals.
    await waitFor(() => expect(briefsCallCount(calls)).toBeGreaterThan(before));
  });

  it("dismisses a recommendation through the dismissals endpoint", async () => {
    const posts: string[] = [];
    const calls: string[] = [];
    stub(posts, calls);
    render(<Goals />);
    await screen.findByText("You ship fast and deploy by hand.");
    const before = briefsCallCount(calls);
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(posts).toContain("POST /api/dismissals"));
    // Dismissing also marks matching recommendations, so /api/briefs must be
    // reloaded here too.
    await waitFor(() => expect(briefsCallCount(calls)).toBeGreaterThan(before));
  });

  it("forces a reassessment", async () => {
    const posts: string[] = [];
    stub(posts);
    render(<Goals />);
    await userEvent.click(await screen.findByRole("button", { name: "Reassess" }));
    await waitFor(() => expect(posts).toContain("POST /api/reassess"));
  });

  it("says so plainly when nothing has changed", async () => {
    stub();
    render(<Goals />);
    expect(await screen.findByText(/No material change since/)).toBeInTheDocument();
  });
});
