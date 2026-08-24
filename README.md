# Quant Intelligence

AI-assisted system that formulates, tests, challenges, and explains quantitative investment hypotheses using reproducible evidence.

**Atelier AI — Experiment 002**

> Build an AI-assisted quantitative research system that formulates, tests, challenges, and explains investment hypotheses using reproducible evidence.

## Status

Phase 1 research foundation plus a deterministic, local paper trader. No live-money trading, brokerage credentials, authentication, or live-data automation is included.

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

### Trader Operations Dashboard

The React application includes a read-only `Trader` view. It observes the local paper trader through:

```text
GET /api/trader/status
GET /api/trader/portfolio
GET /api/trader/decisions?limit=25
GET /api/trader/decisions/{cycle_id}
```

The API reads the persisted `paper_audit/` records by default. Set `QI_PAPER_AUDIT_DIR` and `QI_PAPER_BROKER_STATE` when the paper trader uses another local directory. The dashboard polls status, portfolio, and recent decisions every 15 seconds. It has no order-entry controls and does not calculate signals, risk, fills, portfolio values, returns, or P&L.

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

For a finite scheduled run, use `paper-run`. It defaults to one scheduled cycle; `--cycles` makes the run explicitly bounded and Ctrl+C stops it cleanly:

```bash
quant-intelligence paper-run \
  --data tests/fixtures/paper_cycle.csv \
  --symbol SYNTH --window 3 --initial-capital 1000 \
  --transaction-cost-bps 0 --cycles 1
```

The scheduler, clock, autonomous trader, and status persistence live under `quant_intelligence.trading`. They orchestrate `TradingCycleService`; they do not contain strategy or accounting logic.

## Alpaca paper adapters v0.3

Alpaca integration is edge-only: `AlpacaMarketDataProvider` converts completed daily bars into the existing `Bar`/`MarketDataSnapshot` types, while `AlpacaBroker` implements the existing broker boundary. The strategy, risk gate, cycle service, audit model, and dashboard do not depend on Alpaca SDK objects.

Install the optional runtime dependency with `pip install -e ".[dev]"`. Configure credentials only through environment variables or a local ignored `.env` file:

```text
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
APCA_PAPER=true
APCA_EXECUTION_ENABLED=false
APCA_DATA_FEED=iex
```

`APCA_PAPER` must remain `true`. Execution is disabled by default. The adapter requests daily bars ending at the start of the current UTC date and filters out any bar dated today, so an incomplete current candle cannot reach the SMA strategy. The default `iex` feed is the free feed available to the configured account; it is not a claim that IEX-only data represents the full consolidated market.

Safe connectivity/data smoke test:

```bash
quant-intelligence alpaca-data-check --symbol SPY --window 200
```

Observation mode is the default and never submits an order:

```bash
quant-intelligence alpaca-cycle --symbol SPY --window 200 --observe
```

Paper execution requires both an explicit command flag and server-side configuration:

```bash
APCA_EXECUTION_ENABLED=true \
quant-intelligence alpaca-cycle --symbol SPY --window 200 --execute
```

An Alpaca submission uses a stable client order ID derived from the Quant cycle ID. Known failures become no-trade decisions; timeouts/connection failures become `UNKNOWN` and are reconciled by client order ID without blind retry. Decisions expire while positions persist: a stale intent is never replayed after recovery. `BrokerReconciliation` records expected versus observed order/position state before any synchronization.

## Alpaca autonomous forward paper trader v0.4

`alpaca-run` evolves the existing `AutonomousTrader`; it does not introduce a second execution framework. It uses Alpaca's supported market calendar, waits until a completed daily session plus a five-minute safety delay, reconciles broker state before evaluating SMA, and persists the completed session, next decision, reconciliation state, and halt reason. A session identity is processed at most once, so restarting the process cannot replay the same daily order. The default forward-paper limits are 25% maximum position allocation, $2,500 maximum order notional, and 10 shares per order; all are configurable flags and execution remains disabled unless explicitly enabled.

Use the no-order validation mode first:

```bash
quant-intelligence alpaca-run --symbol SPY --window 200 --dry-run-once
```

After reviewing its reconciliation and scheduling output, an operator can explicitly enable the bounded-risk paper service:

```bash
quant-intelligence alpaca-run --symbol SPY --window 200
```

The service sleeps until the next Alpaca calendar decision time, handles Ctrl+C cleanly, and performs reconciliation-only wakeups while an order is unresolved. `TRADING HEALTH` is persisted separately from `PROCESS RUNNING`; broker, data, calendar, and reconciliation uncertainty halt trading fail-closed. v0.4 assumes a dedicated paper account or an explicitly managed symbol set. Existing positions without a persisted Quant fill are treated as unmanaged and are never liquidated by the strategy.

## PostgreSQL operational persistence

Autonomous operational state can be cut over independently of research JSON. Set `QI_TRADING_PERSISTENCE=postgres` and a standard PostgreSQL `DATABASE_URL`; credentials are never logged or sent to the frontend. The default `json` mode remains available during the transition and does not rewrite or delete existing JSON records.

```bash
export QI_TRADING_PERSISTENCE=postgres
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/quant_intelligence'
alembic upgrade head
uvicorn quant_intelligence.api.main:app --reload
quant-intelligence alpaca-run --symbol SPY --window 200 --dry-run-once
```

To roll back the latest schema revision, use `alembic downgrade -1`. The same `DATABASE_URL` format works with hosted PostgreSQL services such as Neon; no provider-specific API is required. The initial migration creates `trading_cycles`, `orders`, `fills`, `reconciliations`, `managed_positions`, and `operational_status`. Research/backtest result JSON remains file-based.

## Persistent supervised paper service v0.6

`alpaca-service` is the production-oriented, non-interactive entrypoint. It constructs the same `AutonomousTrader`, strategy, risk gate, reconciliation service, and Alpaca PAPER adapters used by `alpaca-run`. Startup validates configuration, checks PostgreSQL, acquires a PostgreSQL advisory lock, starts operational status, and reconciles Alpaca before any strategy cycle. The service checks persistence again before each strategy-triggering cycle and fails closed if durable state cannot be trusted.

Copy `.env.example` to a protected environment file and supply real values outside Git. Required production settings are `QI_TRADING_PERSISTENCE=postgres`, `DATABASE_URL`, `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_PAPER=true`, and an explicitly reviewed `APCA_EXECUTION_ENABLED=true`. Safe runtime settings include `QI_SYMBOL`, `QI_SMA_WINDOW`, the three risk caps, the post-close delay, and the reconciliation interval. Configuration errors never print credentials or the database URL.

Run manually in the foreground first:

```bash
set -a
. /path/to/protected/quant-intelligence.env
set +a
quant-intelligence alpaca-service
```

For a no-order connectivity and scheduling check, retain the existing debugging command:

```bash
quant-intelligence alpaca-run --symbol SPY --window 200 --dry-run-once
```

The deployment template is `deploy/quant-intelligence.service`. Install the Python package or virtual environment so `quant-intelligence` is on the service `PATH`, place the protected environment file at `/etc/quant-intelligence/quant-intelligence.env`, then install and manage the unit explicitly:

```bash
sudo useradd --system --home-dir /var/lib/quant-intelligence --shell /usr/sbin/nologin quant-intelligence
sudo install -d -m 0750 /etc/quant-intelligence
sudo install -m 0600 /path/to/protected/quant-intelligence.env /etc/quant-intelligence/quant-intelligence.env
sudo install -m 0644 deploy/quant-intelligence.service /etc/systemd/system/quant-intelligence.service
sudo systemctl daemon-reload
sudo systemctl enable --now quant-intelligence.service
sudo systemctl status quant-intelligence.service
sudo journalctl -u quant-intelligence.service -f
sudo systemctl restart quant-intelligence.service
sudo systemctl stop quant-intelligence.service
sudo systemctl disable quant-intelligence.service
```

The unit runs as the unprivileged `quant-intelligence` service account, asks systemd to manage `/var/lib/quant-intelligence`, sends SIGTERM for graceful shutdown, records stdout/stderr in the journal, and restarts only after unexpected failure with a 30-second delay and a bounded start rate. It deliberately does not embed credentials, a personal username, repository path, or WSL behavior. Keep `QI_AUDIT_DIR=/var/lib/quant-intelligence/alpaca_audit` for this unit. If a virtual environment is not globally discoverable, set a safe `PATH` in the protected environment file or adapt `ExecStart` during deployment.

The single-instance guard is a PostgreSQL session advisory lock, so it coordinates processes across WSL, Linux hosts, and containers that share the same database and lock name. The lock is released when its dedicated database connection closes; it is not a scheduler or leader-election system, and a future multi-account/distributed design will need explicit leases and fencing. Database and reconciliation failures distinguish a living process from halted trading.

On WSL, systemd must be enabled by the host distribution. Windows sleep, restart, shutdown, or termination of the WSL VM stops the Linux service; systemd can restart it only after WSL itself resumes or starts. The application contains no WSL-specific trading logic and the same entrypoint/unit semantics are portable to a normal Linux host or a future container process supervisor.

## Assumptions and methodology

Signals for day `t` use only bars before day `t`; a 200-day SMA is calculated from closes through `t-1`, and changes execute at day `t` open. Buys use the maximum whole-share quantity affordable after the configured cost; fractional shares are disabled. Costs equal traded notional × bps / 10,000. The benchmark buys whole shares at the first selected bar's open, applies the same cost model, holds through the final close, and leaves residual cash idle.

The internal convention is unadjusted OHLCV as supplied by the provider. Dividend treatment is therefore whatever the supplied series represents; this implementation does not infer or add dividends. Users must document whether their source is adjusted. Metrics annualize trading observations at 252 days. Sharpe assumes a configurable 0% annual risk-free rate in the current engine. CAGR requires more than one observation. These are simplifying assumptions, not claims about real execution.

Backtested performance is hypothetical and does not represent actual trading results. Results must not be marketed as profitable trading strategies.

## Known limitations and roadmap

Phase 1 has no external provider adapter, corporate-action reconciliation, slippage model, fractional shares, multi-asset portfolios, shorting, leverage, intraday data, or AI interpretation. Future work should add provider adapters with explicit adjustment metadata, richer execution/cost models, property-based tests, experiment querying, and only then a constrained AI research layer that consumes the audit contract.
