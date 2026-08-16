export interface StrategySpecification {
  name: string;
  symbol: string;
  start: string | null;
  end: string | null;
  initial_capital: number;
  signal: string;
  signal_parameters: Record<string, unknown>;
  execution_timing: string;
  position_sizing: string;
  transaction_cost_bps: number;
  benchmark: string;
  metadata: Record<string, unknown>;
}
export interface PortfolioState {
  date: string;
  cash: number;
  shares: number;
  asset_value: number;
  equity: number;
  transaction_costs_paid: number;
  exposure: number;
  desired_position: string;
}
export interface Trade {
  date: string;
  symbol: string;
  side: string;
  quantity: number;
  execution_price: number;
  gross_notional: number;
  transaction_cost: number;
  cash_after: number;
  equity_after: number;
  reason: string;
}
export interface EquityPoint {
  date: string;
  equity: number;
}
export interface Metrics {
  total_return: number;
  cagr: number | null;
  annualized_volatility: number | null;
  sharpe_ratio: number | null;
  maximum_drawdown: number;
  number_of_trades: number;
  transaction_costs_paid: number;
  percentage_time_invested: number | null;
  risk_free_rate: number;
  annualization_days: number;
}
export interface BacktestResult {
  specification: StrategySpecification;
  actual_start: string;
  actual_end: string;
  states: PortfolioState[];
  trades: Trade[];
  metrics: Metrics;
  benchmark_metrics: Metrics;
  benchmark_equity: EquityPoint[];
}
