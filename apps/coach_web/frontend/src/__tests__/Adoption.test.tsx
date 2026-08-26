import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Adoption from "../pages/Adoption";

afterEach(() => vi.restoreAllMocks());

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}

const board = {
  features: [
    { name: "plan mode", lesson: "09-advanced-features", status: "never-touched",
      last_used: null, source: "checklist", discovered_at: "2026-01-01",
      checked_off: false, detected_status: "never-touched",
      dismissed: false, dismissal_id: null, history: [] },
    { name: "MCP servers", lesson: "05-mcp", status: "used",
      last_used: "2026-08-01", source: "checklist", discovered_at: "2026-01-01",
      checked_off: false, detected_status: "used",
      dismissed: true, dismissal_id: 42, history: [] },
  ],
};

function stub(calls: string[] = []) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url.startsWith("/api/adoption/board")) return Promise.resolve(jsonResponse(board));
    return Promise.resolve(jsonResponse({ status: "ok" }));
  }));
}

describe("Adoption page", () => {
  it("renders 'Dismiss' for a feature with no dismissal", async () => {
    stub();
    render(<Adoption />);
    await screen.findByText("plan mode");
    const row = screen.getByText("plan mode").closest("tr")!;
    expect(within(row).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("renders 'Undo dismiss' for a dismissed feature", async () => {
    stub();
    render(<Adoption />);
    await screen.findByText("MCP servers");
    const row = screen.getByText("MCP servers").closest("tr")!;
    expect(within(row).getByRole("button", { name: "Undo dismiss" })).toBeInTheDocument();
  });

  it("undoing a dismissal deletes it by id and reloads the board", async () => {
    const calls: string[] = [];
    stub(calls);
    render(<Adoption />);
    await screen.findByText("MCP servers");
    const before = calls.filter((c) => c.startsWith("GET /api/adoption/board")).length;
    const row = screen.getByText("MCP servers").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Undo dismiss" }));
    await waitFor(() => expect(calls).toContain("DELETE /api/dismissals/42"));
    await waitFor(() => expect(
      calls.filter((c) => c.startsWith("GET /api/adoption/board")).length,
    ).toBeGreaterThan(before));
  });

  it("dismissing posts a dismissal and reloads the board", async () => {
    const calls: string[] = [];
    stub(calls);
    render(<Adoption />);
    await screen.findByText("plan mode");
    const before = calls.filter((c) => c.startsWith("GET /api/adoption/board")).length;
    const row = screen.getByText("plan mode").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(calls).toContain("POST /api/dismissals"));
    await waitFor(() => expect(
      calls.filter((c) => c.startsWith("GET /api/adoption/board")).length,
    ).toBeGreaterThan(before));
  });
});
