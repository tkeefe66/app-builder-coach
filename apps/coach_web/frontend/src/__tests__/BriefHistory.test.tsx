import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BriefHistory from "../components/BriefHistory";

const recurring = [
  { target: "deploy-docker", kind: "tag", times: 8, first: "2026-08-01",
    last: "2026-08-09", outcome: "open" },
  { target: "background tasks", kind: "feature", times: 2, first: "2026-08-02",
    last: "2026-08-06", outcome: "converted" },
];

const history = [
  { created_at: "2026-08-09T07:00:00+00:00", day: "2026-08-09", kind: "delta",
    status: "ok", summary: "Shipped the cost page.", error: "",
    recommendations: [{ id: 3, title: "Do it", kind: "tag",
      target: "deploy-docker", why: "w", evidence: "e", outcome: "superseded" }] },
];

describe("BriefHistory", () => {
  it("counts repeat suggestions and names what became of them", () => {
    render(<BriefHistory recurring={recurring} history={history} />);
    expect(screen.getByText(/suggested 8×/)).toBeInTheDocument();
    expect(screen.getByText(/never acted on/)).toBeInTheDocument();
    expect(screen.getByText(/became a goal/)).toBeInTheDocument();
  });

  it("collapses each entry behind a summary line naming its targets", () => {
    render(<BriefHistory recurring={recurring} history={history} />);
    const summary = document.querySelector("details > summary");
    expect(summary).not.toBeNull();
    expect(summary!.textContent).toContain("deploy-docker");
    // The body is present but inside a closed <details>.
    expect(screen.getByText("Shipped the cost page.")).toBeInTheDocument();
    expect(document.querySelector("details")?.open).toBe(false);
  });

  it("shows empty states", () => {
    render(<BriefHistory recurring={[]} history={[]} />);
    expect(screen.getByText("Nothing in the history yet.")).toBeInTheDocument();
  });
});
