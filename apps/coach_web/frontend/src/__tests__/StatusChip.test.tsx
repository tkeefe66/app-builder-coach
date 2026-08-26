import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusChip from "../components/StatusChip";

describe("StatusChip", () => {
  it("always renders the status text", () => {
    for (const s of ["used", "configured-but-unused", "never-touched", "unknown"]) {
      render(<StatusChip status={s} />);
      expect(screen.getByText(s)).toBeInTheDocument();
    }
  });
  it("uses status color only for used/configured", () => {
    const { container } = render(<StatusChip status="never-touched" />);
    expect((container.firstChild as HTMLElement).className).toContain("chip-neutral");
  });
});
