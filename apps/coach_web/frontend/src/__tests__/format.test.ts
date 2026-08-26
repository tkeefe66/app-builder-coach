import { describe, expect, it } from "vitest";
import { fmtDate, fmtWeek, relDays } from "../format";

describe("format", () => {
  it("fmtWeek renders short month-day", () => {
    expect(fmtWeek("2026-07-27")).toBe("Jul 27");
  });
  it("fmtDate handles null", () => {
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate("2026-07-27")).toBe("Jul 27, 2026");
  });
  it("relDays buckets", () => {
    const now = new Date("2026-07-31T12:00:00Z");
    expect(relDays("2026-07-31", now)).toBe("today");
    expect(relDays("2026-07-30", now)).toBe("yesterday");
    expect(relDays("2026-07-27", now)).toBe("4 days ago");
  });
});
