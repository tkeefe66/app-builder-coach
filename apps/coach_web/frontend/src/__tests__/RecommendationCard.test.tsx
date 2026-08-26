import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import RecommendationCard, { type Recommendation } from "../components/RecommendationCard";

function rec(outcome: string): Recommendation {
  return {
    id: 1, title: "Containerize purchase-inventory", kind: "tag",
    target: "deploy-docker", why: "Foundational.",
    evidence: "14 units, zero containers.", outcome,
  };
}

describe("RecommendationCard", () => {
  it("renders an Undo button for a dismissed recommendation when onUndismiss is provided", async () => {
    const onUndismiss = vi.fn();
    const target = rec("dismissed");
    render(<RecommendationCard rec={target} onAdd={vi.fn()} onDismiss={vi.fn()}
      onUndismiss={onUndismiss} />);
    expect(screen.getByText("Dismissed")).toBeInTheDocument();
    const undo = screen.getByRole("button", { name: "Undo" });
    await userEvent.click(undo);
    expect(onUndismiss).toHaveBeenCalledWith(target);
  });

  it("renders no button for a dismissed recommendation when onUndismiss is absent", () => {
    render(<RecommendationCard rec={rec("dismissed")} onAdd={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.getByText("Dismissed")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("never offers Undo for a converted recommendation, even with onUndismiss", () => {
    render(<RecommendationCard rec={rec("converted")} onAdd={vi.fn()} onDismiss={vi.fn()}
      onUndismiss={vi.fn()} />);
    expect(screen.getByText("Added as a goal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });
});
