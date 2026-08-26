import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Cost from "../pages/Cost";

afterEach(() => vi.restoreAllMocks());

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}

describe("Cost page", () => {
  it("renders the true-cost section even when the Claude Code estimate feed is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/truecost")) {
        return Promise.resolve(jsonResponse({
          window_days: 30,
          window: { start: "2026-07-12", end: "2026-08-11" },
          apps: [
            { app: "coach-web", display: "Coach Web", railway_usd: 5.2, llm_usd: 3.1,
              total_usd: 8.3, share: 1 },
          ],
          totals: { railway_usd: 5.2, llm_usd: 3.1, total_usd: 8.3 },
          railway_period: { period_start: null, to_date_usd: 0 },
          llm_month_to_date_usd: 12.5,
          cache: { input_tokens: 1000, cache_read_tokens: 400,
            cache_creation_tokens: 100, read_ratio: 0.4 },
          drift: null,
        }));
      }
      // /api/cost — the Claude Code estimate feed has nothing yet.
      return Promise.resolve(jsonResponse({ available: false, days: [], weekly: [],
        total_usd_window: 0, by_model_window: {} }));
    }));

    render(<Cost />);

    await waitFor(() => {
      expect(screen.getByText("What your apps actually cost")).toBeInTheDocument();
    });
    expect(screen.getByText(/Real billed dollars, not an estimate/)).toBeInTheDocument();
    expect(screen.getByText("Coach Web")).toBeInTheDocument();
    // The Claude Code estimate section keeps its own contained empty state,
    // it must not blank out the true-cost section above it.
    expect(screen.getByText("No cost data yet")).toBeInTheDocument();
  });

  it("always shows the Claude Code estimate heading, even when truecost has no data yet", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/truecost")) {
        // Right after first deploy, before the first v3 sweep lands.
        return Promise.resolve(jsonResponse({
          window_days: 30,
          window: { start: "2026-07-12", end: "2026-08-11" },
          apps: [],
          totals: { railway_usd: 0, llm_usd: 0, total_usd: 0 },
          railway_period: { period_start: null, to_date_usd: 0 },
          llm_month_to_date_usd: 0,
          cache: { input_tokens: 0, cache_read_tokens: 0,
            cache_creation_tokens: 0, read_ratio: 0 },
          drift: null,
        }));
      }
      return Promise.resolve(jsonResponse({ available: false, days: [], weekly: [],
        total_usd_window: 0, by_model_window: {} }));
    }));

    render(<Cost />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Claude Code \(estimated\)/ })).toBeInTheDocument();
    });
    expect(screen.getByText(/Not a bill — you do not pay this/)).toBeInTheDocument();
    // The real-billed-dollars section must not render with no data.
    expect(screen.queryByText("What your apps actually cost")).not.toBeInTheDocument();
  });

  it("reveals per-service rows when an app is expanded", async () => {
    // /api/truecost returns one app with two services; /api/cost has nothing.
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/truecost")) {
        return Promise.resolve(jsonResponse({
          window_days: 30,
          window: { start: "2026-07-12", end: "2026-08-11" },
          apps: [
            { app: "b2b-ai-news", display: "B2B AI News", railway_usd: 12.4,
              llm_usd: 0, total_usd: 12.4, share: 1,
              services: [
                { service: "Postgres", service_id: "s1", total_usd: 7.9,
                  memory_usd: 7.45, share_of_app: 0.64 },
                { service: "web", service_id: "s2", total_usd: 4.5,
                  memory_usd: 4.2, share_of_app: 0.36 },
              ] },
          ],
          totals: { railway_usd: 12.4, llm_usd: 0, total_usd: 12.4 },
          railway_period: { period_start: null, to_date_usd: 0 },
          llm_month_to_date_usd: 0,
          cache: { input_tokens: 0, cache_read_tokens: 0,
            cache_creation_tokens: 0, read_ratio: 0 },
          drift: null,
        }));
      }
      return Promise.resolve(jsonResponse({ available: false, days: [], weekly: [],
        total_usd_window: 0, by_model_window: {} }));
    }));

    render(<Cost />);

    const toggle = await screen.findByRole("button", { name: /B2B AI News/ });
    expect(screen.queryByText(/Postgres/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(await screen.findByText(/Postgres/)).toBeInTheDocument();
    expect(screen.getByText(/memory \$7\.45/)).toBeInTheDocument();
  });

  function truecostWithDrift(drift: unknown) {
    return vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/truecost")) {
        return Promise.resolve(jsonResponse({
          window_days: 30,
          window: { start: "2026-07-12", end: "2026-08-11" },
          apps: [
            { app: "coach-web", display: "Coach Web", railway_usd: 5.2, llm_usd: 3.1,
              total_usd: 8.3, share: 1 },
          ],
          totals: { railway_usd: 5.2, llm_usd: 3.1, total_usd: 8.3 },
          railway_period: { period_start: null, to_date_usd: 0 },
          llm_month_to_date_usd: 12.5,
          cache: { input_tokens: 1000, cache_read_tokens: 400,
            cache_creation_tokens: 100, read_ratio: 0.4 },
          drift,
        }));
      }
      return Promise.resolve(jsonResponse({ available: false, days: [], weekly: [],
        total_usd_window: 0, by_model_window: {} }));
    });
  }

  it("shows the anchor window without a warning while the anchor is fresh", async () => {
    vi.stubGlobal("fetch", truecostWithDrift({
      from: "2026-07-01", to: "2026-07-31", console_usd: 100, tracked_usd: 96,
      gap_usd: 4, age_days: 12, stale: false,
    }));

    render(<Cost />);

    await waitFor(() => {
      expect(screen.getByText("vs Console 2026-07-01→2026-07-31")).toBeInTheDocument();
    });
    expect(screen.queryByText(/re-anchor/)).toBeNull();
  });

  it("flags the tile once the anchor has gone stale", async () => {
    vi.stubGlobal("fetch", truecostWithDrift({
      from: "2026-01-01", to: "2026-01-31", console_usd: 100, tracked_usd: 96,
      gap_usd: 4, age_days: 193, stale: true,
    }));

    const { container } = render(<Cost />);

    await waitFor(() => {
      expect(screen.getByText(/193d old, re-anchor/)).toBeInTheDocument();
      expect(container.querySelector(".tile-warn")).not.toBeNull();
    });
  });
});
