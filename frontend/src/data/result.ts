import type { BacktestResult } from "../types/result";

export async function fetchBacktestResult(
  url: string,
): Promise<BacktestResult> {
  const response = await fetch(url);
  if (!response.ok)
    throw new Error(`Unable to load experiment (${response.status})`);
  const result: unknown = await response.json();
  if (!isBacktestResult(result))
    throw new Error("The experiment result has an unsupported schema");
  return result;
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
