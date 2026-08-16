import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityChartPoint } from "../types/chart";
import { money } from "../utils/format";

export function buildEquityChartData(
  strategy: { date: string; equity: number }[],
  benchmark: { date: string; equity: number }[],
): EquityChartPoint[] {
  const benchmarkByDate = new Map(
    benchmark.map((point) => [point.date, point.equity]),
  );
  return strategy.map((point) => ({
    date: point.date,
    label: point.date,
    strategy: point.equity,
    benchmark: benchmarkByDate.get(point.date) ?? null,
  }));
}

export function EquityChart({ data }: { data: EquityChartPoint[] }) {
  if (!data.length)
    return (
      <div className="empty-state">
        No daily equity observations are available for this experiment.
      </div>
    );
  return (
    <div
      className="chart-shell"
      aria-label="Strategy and benchmark equity curve"
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={1}
        minHeight={1}
      >
        <LineChart
          data={data}
          margin={{ top: 12, right: 18, bottom: 4, left: 0 }}
        >
          <XAxis
            dataKey="label"
            tickFormatter={(value) => value.slice(0, 7)}
            stroke="#6f747e"
            tickLine={false}
            axisLine={false}
            minTickGap={36}
          />
          <YAxis
            stroke="#6f747e"
            tickFormatter={(value) => money(value)}
            tickLine={false}
            axisLine={false}
            width={76}
          />
          <Tooltip
            contentStyle={{
              background: "#15181d",
              border: "1px solid #343a43",
              borderRadius: 4,
            }}
            labelStyle={{ color: "#aeb5bf" }}
            formatter={(value: number, name: string) => [
              money(value),
              name === "strategy" ? "Strategy" : "Buy & hold",
            ]}
          />
          <Line
            type="monotone"
            dataKey="strategy"
            stroke="#d4ae62"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
          />
          <Line
            type="monotone"
            dataKey="benchmark"
            stroke="#8290a5"
            strokeWidth={1.5}
            dot={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
