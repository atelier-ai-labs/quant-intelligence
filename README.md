# Quant Intelligence

AI-assisted system that formulates, tests, challenges, and explains quantitative investment hypotheses using reproducible evidence.

**Atelier AI — Experiment 002**

> Build an AI-assisted quantitative research system that formulates, tests, challenges, and explains investment hypotheses using reproducible evidence.

## Status

Phase 1 foundation. This repository contains deterministic backtesting infrastructure only. No AI, LLM, trading, brokerage, authentication, or live-data automation is included.

## Scope and architecture

Phase 1 supports daily US equity/ETF OHLCV data, long/cash states, one asset, integer shares, next-open execution, and configurable basis-point transaction costs. Responsibilities are separated into normalized data validation/providers, typed models, strategies, portfolio costs, the sequential backtest engine, metrics, benchmarks, and JSON experiment persistence.

## Installation and usage

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Run a CSV-backed reference experiment:

```bash
quant-intelligence backtest --data data/SPY.csv --symbol SPY --window 200 \
  --start 2015-01-01 --end 2025-12-31 --initial-capital 10000 \
  --transaction-cost-bps 5 --output experiments/spy.json
```

The CSV must contain `date,open,high,low,close,volume`. The CLI prints a concise summary and persists the full audit result as JSON.

## Assumptions and methodology

Signals for day `t` use only bars before day `t`; a 200-day SMA is calculated from closes through `t-1`, and changes execute at day `t` open. Buys use the maximum whole-share quantity affordable after the configured cost; fractional shares are disabled. Costs equal traded notional × bps / 10,000. The benchmark buys whole shares at the first selected bar's open, applies the same cost model, holds through the final close, and leaves residual cash idle.

The internal convention is unadjusted OHLCV as supplied by the provider. Dividend treatment is therefore whatever the supplied series represents; this implementation does not infer or add dividends. Users must document whether their source is adjusted. Metrics annualize trading observations at 252 days. Sharpe assumes a configurable 0% annual risk-free rate in the current engine. CAGR requires more than one observation. These are simplifying assumptions, not claims about real execution.

Backtested performance is hypothetical and does not represent actual trading results. Results must not be marketed as profitable trading strategies.

## Known limitations and roadmap

Phase 1 has no external provider adapter, corporate-action reconciliation, slippage model, fractional shares, multi-asset portfolios, shorting, leverage, intraday data, or AI interpretation. Future work should add provider adapters with explicit adjustment metadata, richer execution/cost models, property-based tests, experiment querying, and only then a constrained AI research layer that consumes the audit contract.
