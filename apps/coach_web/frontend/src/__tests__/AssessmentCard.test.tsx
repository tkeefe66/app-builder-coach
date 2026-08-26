import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AssessmentCard, { type Assessment } from "../components/AssessmentCard";

const rec = {
  id: 1, title: "Containerize purchase-inventory", kind: "tag",
  target: "deploy-docker", why: "It is foundational.",
  evidence: "14 units across 5 repos, zero container work.", outcome: "open",
};

const assessment: Assessment = {
  created_at: "2026-08-11T07:00:00+00:00", day: "2026-08-11", kind: "assessment",
  status: "ok", summary: "You ship fast and deploy by hand.", error: "",
  stale: false, recommendations: [rec],
};

describe("AssessmentCard", () => {
  it("shows the summary, the recommendation, and its evidence", () => {
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText("You ship fast and deploy by hand.")).toBeInTheDocument();
    expect(screen.getByText("Containerize purchase-inventory")).toBeInTheDocument();
    expect(screen.getByText(/zero container work/)).toBeInTheDocument();
  });

  it("hands the whole recommendation to onAdd", async () => {
    const onAdd = vi.fn();
    render(<AssessmentCard assessment={assessment} onAdd={onAdd}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Add as goal" }));
    expect(onAdd).toHaveBeenCalledWith(rec);
  });

  it("hands the recommendation to onDismiss", async () => {
    const onDismiss = vi.fn();
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={onDismiss} onReassess={vi.fn()} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledWith(rec);
  });

  it("flags a stale assessment and shows the error", () => {
    render(<AssessmentCard assessment={{ ...assessment, stale: true,
      error: "RuntimeError: 429" }} onAdd={vi.fn()} onDismiss={vi.fn()}
      onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText(/could not be regenerated/)).toBeInTheDocument();
    expect(screen.getByText(/429/)).toBeInTheDocument();
  });

  it("shows an empty state when there is no assessment yet", () => {
    render(<AssessmentCard assessment={null} onAdd={vi.fn()} onDismiss={vi.fn()}
      onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText("No assessment yet")).toBeInTheDocument();
  });

  it("disables Reassess while one is running", () => {
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /Reassessing/ })).toBeDisabled();
  });

  it("shows a status line instead of buttons for a converted recommendation", () => {
    const converted = { ...rec, outcome: "converted" };
    render(<AssessmentCard assessment={{ ...assessment, recommendations: [converted] }}
      onAdd={vi.fn()} onDismiss={vi.fn()} onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText("Added as a goal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add as goal" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
  });
});
