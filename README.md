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

## Application API and frontend

The v0.1 API lives inside the Python package and provides a thin application boundary over the existing engine. It uses a filesystem-backed `experiments/` store, exposes only health/list/detail/create endpoints, and does not recalculate financial metrics. The frontend now reads experiment summaries and canonical results through this API rather than fetching persisted JSON directly.

Start the API from the repository root:

```bash
source .venv/bin/activate
uvicorn quant_intelligence.api.main:app --reload
```

Then run the frontend:

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

The API allows only the local Vite origins (`localhost:5173` and `127.0.0.1:5173`) through CORS. Set `VITE_API_BASE_URL` only when the API is hosted on a different origin; same-host local development uses the empty default. The frontend selects the newest persisted experiment from `GET /api/experiments`, then retrieves its canonical result from `GET /api/experiments/{experiment_id}`.

Create an experiment through the API with a local CSV:

```bash
curl -X POST http://localhost:8000/api/experiments \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"SPY","strategy":"sma_trend","parameters":{"window":200},"start":"2015-01-01","end":"2025-12-31","initial_capital":10000,"transaction_cost_bps":5,"benchmark":"buy_and_hold","data_path":"./data/spy.csv"}'
```

The backend result includes `benchmark_equity`, the dated buy-and-hold equity series required for a truthful comparison chart. The application remains intentionally local and filesystem-backed; no database or background job system is included.

## Paper Trader v0.1

The first paper-trading cycle is available as a deterministic CLI command. It uses completed local CSV bars, the existing SMA strategy, a fail-closed risk gate, an in-memory `PaperBroker`, and a JSON audit record. It does not schedule itself and does not connect to a brokerage.

```bash
source .venv/bin/activate
quant-intelligence paper-cycle \
  --data tests/fixtures/paper_cycle.csv \
  --symbol SYNTH --window 3 --initial-capital 1000 \
  --transaction-cost-bps 0 \
  --audit-dir /tmp/quant-intelligence-paper-audit \
  --timestamp 2020-01-04T12:00:00+00:00
```

The fixture produces a BUY for 76 whole shares at the completed close of 13, passes the risk gate, fills in the paper broker, leaves $12 cash, and persists the full decision record plus broker state under the audit directory. Repeating the same cycle identity returns the prior decision rather than submitting a second order.

## Assumptions and methodology

Signals for day `t` use only bars before day `t`; a 200-day SMA is calculated from closes through `t-1`, and changes execute at day `t` open. Buys use the maximum whole-share quantity affordable after the configured cost; fractional shares are disabled. Costs equal traded notional × bps / 10,000. The benchmark buys whole shares at the first selected bar's open, applies the same cost model, holds through the final close, and leaves residual cash idle.

The internal convention is unadjusted OHLCV as supplied by the provider. Dividend treatment is therefore whatever the supplied series represents; this implementation does not infer or add dividends. Users must document whether their source is adjusted. Metrics annualize trading observations at 252 days. Sharpe assumes a configurable 0% annual risk-free rate in the current engine. CAGR requires more than one observation. These are simplifying assumptions, not claims about real execution.

Backtested performance is hypothetical and does not represent actual trading results. Results must not be marketed as profitable trading strategies.

## Known limitations and roadmap

Phase 1 has no external provider adapter, corporate-action reconciliation, slippage model, fractional shares, multi-asset portfolios, shorting, leverage, intraday data, or AI interpretation. Future work should add provider adapters with explicit adjustment metadata, richer execution/cost models, property-based tests, experiment querying, and only then a constrained AI research layer that consumes the audit contract.
