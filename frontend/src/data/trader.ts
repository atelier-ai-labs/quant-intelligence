import type {
  DecisionSummary,
  TraderPortfolio,
  TraderStatus,
  TradingDecision,
} from "../types/trader";

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok)
    throw new Error(`Unable to load trader data (${response.status})`);
  return (await response.json()) as T;
}

export const fetchTraderStatus = () =>
  getJson<TraderStatus>("/api/trader/status");
export const fetchTraderPortfolio = () =>
  getJson<TraderPortfolio>("/api/trader/portfolio");
export const fetchTraderDecisions = (limit = 25) =>
  getJson<DecisionSummary[]>(`/api/trader/decisions?limit=${limit}`);
export const fetchTraderDecision = (cycleId: string) =>
  getJson<TradingDecision>(`/api/trader/decisions/${cycleId}`);
