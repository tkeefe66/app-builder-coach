export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
}
export const series1 = () => cssVar("--series-1");
export const gridColor = () => cssVar("--grid");
export const mutedColor = () => cssVar("--muted");
