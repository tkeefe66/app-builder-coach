import { Bar, BarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis } from "recharts";
import { fmtWeek } from "../format";
import { cssVar, gridColor, mutedColor, series1 } from "../tokens";

export default function WeeklyBars({ data, height = 220,
  allowDecimals = false, name = "commits" }: {
  data: { start: string; commits: number }[]; height?: number;
  // Cost reuses this chart with dollars on the y axis.
  allowDecimals?: boolean; name?: string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={gridColor()} />
        <XAxis dataKey="start" tickFormatter={fmtWeek}
          tick={{ fill: mutedColor(), fontSize: 12 }}
          axisLine={false} tickLine={false} />
        <YAxis allowDecimals={allowDecimals} width={32}
          tick={{ fill: mutedColor(), fontSize: 12 }}
          axisLine={false} tickLine={false} />
        <Tooltip
          labelFormatter={(v) => fmtWeek(String(v))}
          cursor={{ fill: "transparent" }}
          contentStyle={{
            background: cssVar("--surface"),
            border: `1px solid ${cssVar("--border")}`,
            borderRadius: 6,
            color: cssVar("--ink"),
            fontSize: 12,
          }}
          labelStyle={{ color: cssVar("--ink-2") }}
          itemStyle={{ color: cssVar("--ink") }}
        />
        <Bar dataKey="commits" name={name} fill={series1()} radius={[4, 4, 0, 0]}
          maxBarSize={28} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
