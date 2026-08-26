import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import GoalPicker from "../components/GoalPicker";

const gaps = {
  never_built: ["deploy-docker", "websockets-sse"],
  stale: ["scraping"],
  adoption_gaps: ["background tasks"],
};

describe("GoalPicker", () => {
  it("offers no free-text field", () => {
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={vi.fn()} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("adds a tag gap with a generated title", async () => {
    const onAdd = vi.fn();
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={onAdd} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add deploy-docker" }));
    expect(onAdd).toHaveBeenCalledWith({
      kind: "tag", target: "deploy-docker", title: "Build something with deploy-docker",
    });
  });

  it("adds a feature gap with an adoption title", async () => {
    const onAdd = vi.fn();
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={onAdd} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add background tasks" }));
    expect(onAdd).toHaveBeenCalledWith({
      kind: "feature", target: "background tasks", title: "Adopt background tasks",
    });
  });

  it("adds a stale tag gap with a come-back-to title", async () => {
    const onAdd = vi.fn();
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={onAdd} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add scraping" }));
    expect(onAdd).toHaveBeenCalledWith({
      kind: "tag", target: "scraping", title: "Come back to scraping",
    });
  });

  it("hides gaps that are already goals", () => {
    render(<GoalPicker gaps={gaps} onAdd={vi.fn()} onDone={vi.fn()} onDelete={vi.fn()}
      goals={[{ id: 1, kind: "tag", target: "deploy-docker",
        title: "Containerize", target_date: "", status: "active" }]} />);
    expect(screen.queryByRole("button", { name: "Add deploy-docker" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add websockets-sse" })).toBeInTheDocument();
  });

  it("lists active goals with done and delete", async () => {
    const onDone = vi.fn();
    render(<GoalPicker gaps={gaps} onAdd={vi.fn()} onDone={onDone} onDelete={vi.fn()}
      goals={[{ id: 7, kind: "tag", target: "deploy-docker",
        title: "Containerize", target_date: "", status: "active" }]} />);
    expect(screen.getByText("Containerize")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalledWith(7);
  });
});
