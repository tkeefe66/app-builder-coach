import { Line, LineChart, ResponsiveContainer } from "recharts";
import { series1 } from "../tokens";

export default function Sparkline({ data }: { data: { month: string; count: number }[] }) {
  return (
    <ResponsiveContainer width={120} height={28}>
      <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line dataKey="count" stroke={series1()} strokeWidth={2}
          dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
