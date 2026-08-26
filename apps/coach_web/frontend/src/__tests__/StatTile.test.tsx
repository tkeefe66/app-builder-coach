import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatTile from "../components/StatTile";

describe("StatTile", () => {
  it("renders plainly by default", () => {
    const { container } = render(<StatTile label="Untracked spend" value="$4.00" sub="vs Console" />);
    expect(screen.getByText("$4.00")).toBeInTheDocument();
    expect(container.querySelector(".tile-warn")).toBeNull();
  });

  it("marks the tile and its sub-label when warn is set", () => {
    const { container } = render(
      <StatTile label="Untracked spend" value="$4.00" sub="180d old" warn />);
    expect(container.querySelector(".tile-warn")).not.toBeNull();
    expect(screen.getByText("180d old").className).toContain("warn-text");
  });
});
