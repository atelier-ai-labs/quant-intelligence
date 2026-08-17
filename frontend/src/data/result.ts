import type { BacktestResult, ExperimentSummary } from "../types/result";

const apiBase = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchExperimentSummaries(): Promise<ExperimentSummary[]> {
  const response = await fetch(`${apiBase}/api/experiments`);
  if (!response.ok)
    throw new Error(`Unable to load experiments (${response.status})`);
  const result: unknown = await response.json();
  if (!Array.isArray(result) || !result.every(isExperimentSummary))
    throw new Error("The experiment list has an unsupported schema");
  return result;
}

export async function fetchBacktestResult(
  url: string,
): Promise<BacktestResult> {
  const response = await fetch(url.startsWith("/") ? `${apiBase}${url}` : url);
  if (!response.ok)
    throw new Error(`Unable to load experiment (${response.status})`);
  const result: unknown = await response.json();
  if (!isBacktestResult(result))
    throw new Error("The experiment result has an unsupported schema");
  return result;
}

function isExperimentSummary(value: unknown): value is ExperimentSummary {
  if (!value || typeof value !== "object") return false;
  const summary = value as Partial<ExperimentSummary>;
  return (
    typeof summary.experiment_id === "string" &&
    typeof summary.symbol === "string" &&
    typeof summary.strategy === "string" &&
    typeof summary.actual_start === "string" &&
    typeof summary.actual_end === "string" &&
    typeof summary.initial_capital === "number"
  );
}

function isBacktestResult(value: unknown): value is BacktestResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<BacktestResult>;
  return (
    typeof result.actual_start === "string" &&
    typeof result.actual_end === "string" &&
    !!result.specification &&
    !!result.metrics &&
    Array.isArray(result.states) &&
    Array.isArray(result.trades)
  );
}
