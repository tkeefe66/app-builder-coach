import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GradeCard, { type Grade } from "../components/GradeCard";

const grade: Grade = {
  level: "junior", level_label: "Junior Engineer",
  next_level: "mid", next_label: "Mid-Level Engineer",
  percent_to_next: 72,
  gaps: [
    { tag: "deploy-docker",
      have: { count: 0, avg_complexity: null, last_done: null },
      need: { min_count: 2, min_avg_complexity: null, within_days: null },
      best_fit_repo: "coach-web" },
    { tag: "auth",
      have: { count: 3, avg_complexity: 3.7, last_done: "2026-07-23" },
      need: { min_count: 5, min_avg_complexity: 3.0, within_days: null },
      best_fit_repo: "budget-app" },
  ],
};

describe("GradeCard", () => {
  it("shows empty state when grade is null", () => {
    render(<GradeCard grade={null} />);
    expect(screen.getByText("No data yet.")).toBeInTheDocument();
  });
  it("renders level, progress, gap lines, and caption", () => {
    render(<GradeCard grade={grade} />);
    expect(screen.getByText("Junior Engineer")).toBeInTheDocument();
    expect(screen.getByText("72% to Mid-Level Engineer")).toBeInTheDocument();
    expect(screen.getByText(/deploy-docker \(never built\)/)).toBeInTheDocument();
    expect(screen.getByText(/auth \(3 builds — need 5\+\)/)).toBeInTheDocument();
    expect(screen.getByText(/best fit: coach-web/)).toBeInTheDocument();
    expect(screen.getByText(/not years of experience/)).toBeInTheDocument();
  });
  it("renders top-of-ladder state without a target", () => {
    render(<GradeCard grade={{ ...grade, next_level: null, next_label: null,
      percent_to_next: 100, gaps: [] }} />);
    expect(screen.getByText("Top of the ladder.")).toBeInTheDocument();
  });
  it("names recency (not count) as the gap when count and complexity are satisfied", () => {
    const oldDate = "2026-01-01"; // well over any within_days window, relative to "today"
    const recencyGrade: Grade = {
      ...grade,
      gaps: [{
        tag: "auth",
        have: { count: 12, avg_complexity: 4, last_done: oldDate },
        need: { min_count: 1, min_avg_complexity: null, within_days: 30 },
        best_fit_repo: "coach-web",
      }],
    };
    render(<GradeCard grade={recencyGrade} />);
    expect(screen.getByText(/auth \(12 builds, last done .*refresh it\)/))
      .toBeInTheDocument();
  });
  it("shows at most 3 gap items even when more are present", () => {
    const fourGaps: Grade = {
      ...grade,
      gaps: [
        { tag: "a", have: { count: 0, avg_complexity: null, last_done: null },
          need: { min_count: 1, min_avg_complexity: null, within_days: null },
          best_fit_repo: "r" },
        { tag: "b", have: { count: 0, avg_complexity: null, last_done: null },
          need: { min_count: 1, min_avg_complexity: null, within_days: null },
          best_fit_repo: "r" },
        { tag: "c", have: { count: 0, avg_complexity: null, last_done: null },
          need: { min_count: 1, min_avg_complexity: null, within_days: null },
          best_fit_repo: "r" },
        { tag: "d", have: { count: 0, avg_complexity: null, last_done: null },
          need: { min_count: 1, min_avg_complexity: null, within_days: null },
          best_fit_repo: "r" },
      ],
    };
    const { container } = render(<GradeCard grade={fourGaps} />);
    expect(container.querySelectorAll("li")).toHaveLength(3);
  });
});
