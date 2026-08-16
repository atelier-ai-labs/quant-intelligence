export interface EquityChartPoint {
  date: string;
  label: string;
  strategy: number;
  benchmark: number | null;
}
