export type TraderStatus = {
  available: boolean;
  mode: "paper";
  state?: "running" | "stopped" | string;
  last_cycle_id?: string | null;
  last_cycle_timestamp?: string | null;
  last_cycle_outcome?: string | null;
  last_error?: string | null;
  current_equity?: number | null;
  current_cash?: number | null;
  current_positions?: Position[];
  most_recent_market_data_timestamp?: string | null;
  reason?: string;
};

export type Position = {
  symbol: string;
  quantity?: number;
  shares?: number;
  average_price?: number | null;
  market_price?: number | null;
  market_value?: number | null;
};

export type TraderPortfolio = {
  available: boolean;
  mode: "paper";
  cash?: number | null;
  equity?: number | null;
  transaction_costs_paid?: number | null;
  positions: Position[];
  market_data_timestamp?: string | null;
  valuation_note?: string;
  reason?: string;
};

export type DecisionSummary = {
  cycle_id: string;
  timestamp: string;
  symbol: string;
  signal: string;
  outcome: string;
  risk: { approved: boolean; reason: string };
  order: { side: string; quantity: number; status: string | null } | null;
  fill: { price: number; quantity: number; transaction_cost: number } | null;
  error: string | null;
};

export type TradingDecision = DecisionSummary & {
  strategy: string;
  strategy_parameters: Record<string, unknown>;
  data_timestamp: string | null;
  signal_reason: string;
  portfolio_before: PortfolioSnapshot | null;
  proposed_order: OrderIntent | null;
  risk_decision: RiskDecision;
  submitted_order: SubmittedOrder | null;
  portfolio_after: PortfolioSnapshot | null;
};

export type OrderIntent = {
  symbol: string;
  side: string;
  quantity: number;
  order_type: string;
  asset_type: string;
  reason: string;
};

export type RiskDecision = {
  approved: boolean;
  reason: string;
  intent: OrderIntent | null;
};

export type SubmittedOrder = {
  order_id: string;
  intent: OrderIntent;
  submitted_at: string;
  status: string;
};

export type PortfolioSnapshot = {
  timestamp: string;
  cash: number;
  positions: Position[];
  asset_value: number;
  equity: number;
  transaction_costs_paid: number;
};
