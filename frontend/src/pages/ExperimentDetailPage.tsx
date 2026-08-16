import { useEffect, useState } from "react";
import { fetchBacktestResult } from "../data/result";
import { EquityChart, buildEquityChartData } from "../components/EquityChart";
import { DailyStateTable } from "../components/DailyStateTable";
import { MethodologyPanel } from "../components/MethodologyPanel";
import { MetricCard } from "../components/MetricCard";
import { TradeTable } from "../components/TradeTable";
import type { BacktestResult } from "../types/result";
import { dateLabel, decimal, money, percent } from "../utils/format";

type Tab = "trades" | "states" | "methodology";
export function ExperimentDetailPage({ resultUrl }: { resultUrl: string }) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("trades");
  useEffect(() => {
    let active = true;
    setResult(null);
    setError(null);
    fetchBacktestResult(resultUrl)
      .then((value) => active && setResult(value))
      .catch(
        (reason: unknown) =>
          active &&
          setError(
            reason instanceof Error
              ? reason.message
              : "Unable to load experiment",
          ),
      );
    return () => {
      active = false;
    };
  }, [resultUrl]);
  if (error)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Experiment unavailable</span>
          <h1>Could not load this result</h1>
          <p>{error}</p>
          <p className="muted">
            Check the configured result URL and confirm the backend has produced
            a persisted JSON result.
          </p>
        </div>
      </main>
    );
  if (!result)
    return (
      <main className="page">
        <div className="state-panel">
          <span className="eyebrow">Quant Intelligence</span>
          <h1>Loading experiment</h1>
          <div className="loading-bar" />
        </div>
      </main>
    );
  const { specification: spec, metrics } = result;
  const endingEquity = result.states.at(-1)?.equity ?? null;
  const chartData = buildEquityChartData(
    result.states,
    result.benchmark_equity ?? [],
  );
  const tone = (value: number | null) =>
    value != null && value >= 0
      ? "positive"
      : value != null
        ? "negative"
        : "neutral";
  const isDemo = spec.metadata?.demo === true;
  return (
    <main className="page">
      {isDemo && (
        <div className="demo-notice">
          Local synthetic validation result · replace{" "}
          <code>VITE_EXPERIMENT_RESULT_URL</code> with a persisted backend
          result for research use.
        </div>
      )}
      <header className="experiment-header">
        <div>
          <div className="eyebrow">Atelier AI · Quant Intelligence</div>
          <h1>{spec.name}</h1>
          <div className="experiment-subtitle">
            <span className="symbol-chip">{spec.symbol}</span>
            <span>
              {dateLabel(result.actual_start)} — {dateLabel(result.actual_end)}
            </span>
            <span className="status-dot">Completed result</span>
          </div>
        </div>
        <div className="header-meta">
          <span>Signal</span>
          <strong>{spec.signal.replaceAll("_", " ")}</strong>
          <span>Benchmark</span>
          <strong>{spec.benchmark.replaceAll("_", " ")}</strong>
        </div>
      </header>
      <section className="spec-strip">
        <div>
          <span>Initial capital</span>
          <strong>{money(spec.initial_capital)}</strong>
        </div>
        <div>
          <span>Transaction costs</span>
          <strong>{decimal(spec.transaction_cost_bps)} bps</strong>
        </div>
        <div>
          <span>Execution</span>
          <strong>{spec.execution_timing.replaceAll("_", " ")}</strong>
        </div>
        <div>
          <span>Parameters</span>
          <strong>
            {Object.entries(spec.signal_parameters)
              .map(([key, value]) => `${key}: ${String(value)}`)
              .join(" · ")}
          </strong>
        </div>
      </section>
      <section className="metric-grid">
        <MetricCard label="Final equity" value={money(endingEquity)} />
        <MetricCard
          label="Total return"
          value={percent(metrics.total_return)}
          tone={tone(metrics.total_return)}
        />
        <MetricCard
          label="CAGR"
          value={percent(metrics.cagr)}
          tone={tone(metrics.cagr)}
        />
        <MetricCard
          label="Annualized volatility"
          value={percent(metrics.annualized_volatility)}
        />
        <MetricCard
          label="Sharpe ratio"
          value={decimal(metrics.sharpe_ratio)}
          detail={`Risk-free rate ${percent(metrics.risk_free_rate)}`}
        />
        <MetricCard
          label="Maximum drawdown"
          value={percent(metrics.maximum_drawdown)}
          tone="negative"
        />
        <MetricCard label="Trades" value={String(metrics.number_of_trades)} />
        <MetricCard
          label="Time invested"
          value={percent(metrics.percentage_time_invested)}
        />
        <MetricCard
          label="Costs paid"
          value={money(metrics.transaction_costs_paid)}
        />
      </section>
      <section className="panel chart-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Evidence</span>
            <h2>Equity curve</h2>
          </div>
          <div className="legend">
            <span>
              <i className="legend-line strategy-line" />
              Strategy
            </span>
            <span>
              <i className="legend-line benchmark-line" />
              Buy &amp; hold
            </span>
          </div>
        </div>
        {result.benchmark_equity?.length ? (
          <EquityChart data={chartData} />
        ) : (
          <div className="empty-state">
            Benchmark equity history is not available in this result. The
            benchmark metrics are preserved below, but the comparison line
            cannot be fabricated.
          </div>
        )}
      </section>
      <section className="panel">
        <div className="tabs" role="tablist">
          {(
            [
              ["trades", `Trades (${result.trades.length})`],
              ["states", `Daily states (${result.states.length})`],
              ["methodology", "Methodology"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              className={tab === value ? "tab active" : "tab"}
              role="tab"
              aria-selected={tab === value}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="tab-content">
          {tab === "trades" && <TradeTable trades={result.trades} />}
          {tab === "states" && <DailyStateTable states={result.states} />}
          {tab === "methodology" && <MethodologyPanel />}
        </div>
      </section>
      <footer className="page-footer">
        Research output · No autonomous investment recommendation is made by
        this interface.
      </footer>
    </main>
  );
}
