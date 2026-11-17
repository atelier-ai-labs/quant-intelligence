# Quant Intelligence

Quant Intelligence is my sandbox for testing trading ideas honestly. You give it an idea ("buy SPY when it's above its 200-day average, sit in cash when it isn't"), and it backtests the idea, shows you the evidence, and then trades it on a paper account so you can see how it behaves in the real market without risking real money.

It's Experiment 002 at Atelier AI. The long-term goal is an AI research assistant that can come up with hypotheses, test them, poke holes in them, and explain the results. Before any of that can be trusted, the boring parts have to be right: the math, the timing, the record-keeping, and the safety rails. That's what most of this repo is right now.

**No real money is involved.** Live trading is off by default and can't be turned on by accident.

## Where things stand (November 2026)

- The backtester works for one stock or ETF on daily bars, with long-or-cash positions, whole shares, and simple transaction costs.
- A paper trader runs every trading day against an [Alpaca](https://alpaca.markets/) paper account, using the same strategy and a strict risk gate.
- All of the trader's state lives in PostgreSQL (I use Neon), so it survives restarts and never places the same order twice.
- **Current milestone: v0.8 forward soak.** The service has been running since August 21. As of Nov 17 it has logged 43 daily cycles with no duplicate orders, no unresolved orders, no reconciliation problems, and no risk halts. Every decision so far has been HOLD, which is fine, because this phase is about proving the plumbing is reliable, not about making money.
- **Next up:** finish collecting soak evidence across more sessions and restarts, then start v0.9, which is about smarter strategies.

There's also a read-only Robinhood adapter in `trading/robinhood_adapter.py`. It was an experiment, it can't place orders, and it isn't wired into anything.

[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) has the detailed engineering state, and [`METHODOLOGY.md`](METHODOLOGY.md) explains exactly how the backtest math works.

## How it works

Every trading day the paper trader goes through the same steps:

1. **Wait for the market to close.** It uses Alpaca's market calendar and waits five extra minutes so it only ever sees completed daily bars. Today's half-finished candle never reaches the strategy.
2. **Check in with the broker first.** Before deciding anything, it compares what it thinks it owns with what Alpaca says it owns. If they don't match, or it can't tell, it stops for the day.
3. **Ask the strategy.** Right now that's a simple moving-average trend rule. The strategy only says what it *wants* to do. It can't place orders.
4. **Run it through the risk gate.** The gate decides whether the trade is allowed: no position over 20% of equity, no leverage, a 5% daily loss breaker, and a hard stop at a 30% drawdown from the peak.
5. **Submit, then confirm.** Every order gets an ID derived from the day's cycle, so a retry or restart can't send it twice. If a submission times out, the trader looks the order up instead of guessing.
6. **Write everything down.** The decision, the risk check, the order, and the fill all go into the database, so you can trace any trade from start to finish.

## The ground rules

These matter more than any feature:

- **Alpaca is the source of truth** for orders, fills, and positions. Quant Intelligence keeps its own copy, but it never trusts that copy over the broker.
- **When in doubt, don't trade.** If the broker, the market data, the calendar, or the database looks off, the trader records a no-trade and halts. It doesn't guess.
- **Strategies can't place orders.** Only the risk and execution layers can.
- **The backend owns the numbers.** The dashboard displays results. It never recalculates returns or P&L on its own.
- **Anything touching trading, risk, accounting, or persistence needs tests.**
- **Live money stays off** unless someone deliberately and explicitly turns it on.

## Getting started

You'll need Python 3.11+ and Node (only if you want the dashboard).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite runs in a couple of seconds and doesn't need a database or any API keys.

### Run a backtest

Point it at a CSV with `date,open,high,low,close,volume` columns:

```bash
quant-intelligence backtest --data data/SPY.csv --symbol SPY --window 200 \
  --start 2015-01-01 --end 2025-12-31 --initial-capital 10000 \
  --transaction-cost-bps 5 --output experiments/spy.json
```

You get a short summary in the terminal, and the full result (trades, equity curve, metrics, and a buy-and-hold comparison) is saved as JSON.

### Open the dashboard

Start the API:

```bash
uvicorn quant_intelligence.api.main:app --reload
```

Then start the frontend in another terminal:

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open `http://localhost:5173`. The dashboard has two views:

- **Research** shows your latest backtest compared to buy-and-hold.
- **Trader** is a read-only window into the paper trader: status, portfolio, and recent decisions, refreshed every 15 seconds. It has no buttons that place orders, on purpose.

You can also create a backtest through the API:

```bash
curl -X POST http://localhost:8000/api/experiments \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"SPY","strategy":"sma_trend","parameters":{"window":200},"start":"2015-01-01","end":"2025-12-31","initial_capital":10000,"transaction_cost_bps":5,"benchmark":"buy_and_hold","data_path":"./data/spy.csv"}'
```

The API only accepts requests from the local Vite dev server. If the API runs somewhere else, set `VITE_API_BASE_URL` in the frontend. If the trader writes its files to a non-default location, point the API at them with `QI_PAPER_AUDIT_DIR` and `QI_PAPER_BROKER_STATE`.

## Paper trading

### Try it offline first

You can run one paper-trading cycle against a tiny sample file, with no broker and no network:

```bash
quant-intelligence paper-cycle \
  --data tests/fixtures/paper_cycle.csv \
  --symbol SYNTH --window 3 --initial-capital 1000 \
  --transaction-cost-bps 0 \
  --audit-dir /tmp/quant-intelligence-paper-audit \
  --timestamp 2020-01-04T12:00:00+00:00
```

In this example the strategy wants to buy, and the risk gate says no because the position would be bigger than 20% of the account. That's the point of the example. Run it again and you get the same decision back instead of a second order. `paper-run` does the same thing on a schedule, for a fixed number of cycles.

### Connect to Alpaca

Copy `.env.example` to `.env` (it's git-ignored) and fill in your **paper** account keys:

```text
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
APCA_PAPER=true
APCA_EXECUTION_ENABLED=false
APCA_DATA_FEED=iex
```

`APCA_PAPER` has to stay `true`. `APCA_EXECUTION_ENABLED=false` means the trader can look but can't submit orders. The `iex` feed is Alpaca's free data feed. It's good enough for daily bars, but it isn't the full consolidated market.

Then work up to placing orders one step at a time:

```bash
# 1. Can we reach Alpaca and pull data?
quant-intelligence alpaca-data-check --symbol SPY --window 200

# 2. Run one cycle and show what it *would* do. Never places an order.
quant-intelligence alpaca-cycle --symbol SPY --window 200 --observe

# 3. Do a full dry run of the scheduler and reconciliation. Still no orders.
quant-intelligence alpaca-run --symbol SPY --window 200 --dry-run-once

# 4. Actually place paper orders. This needs the flag AND the setting.
APCA_EXECUTION_ENABLED=true quant-intelligence alpaca-cycle --symbol SPY --window 200 --execute
```

Pre-existing positions in the account that Quant Intelligence didn't buy are left alone. It will never sell something it didn't open.

### Set up the database

The long-running trader keeps its state in PostgreSQL. Any Postgres works; I use [Neon](https://neon.tech/).

```bash
export QI_TRADING_PERSISTENCE=postgres
export DATABASE_URL='postgresql+psycopg://user:password@host:5432/quant_intelligence'
alembic upgrade head
```

`alembic downgrade -1` undoes the latest migration. The database URL and keys are never logged or sent to the dashboard. Backtest results are still plain JSON files in `experiments/`.

## Running the trader for real (on paper)

`alpaca-service` is the long-running version. On startup it checks its config, connects to the database, takes a lock so only one copy can run at a time, and reconciles with Alpaca before doing anything else. Then it sleeps until the next market close, runs the cycle, and repeats.

Run it in the foreground the first time so you can watch it:

```bash
set -a; . /path/to/your/quant-intelligence.env; set +a
alembic upgrade head
quant-intelligence trading-status --json
quant-intelligence alpaca-run --symbol "$QI_SYMBOL" --window "$QI_SMA_WINDOW" --dry-run-once
quant-intelligence alpaca-service
```

`.env.example` lists every setting: the symbol, the SMA window, the risk limits, the post-close delay, and how often it re-checks an unresolved order.

### As a systemd service

There's a unit file in [`deploy/quant-intelligence.service`](deploy/quant-intelligence.service). It runs as its own unprivileged user, shuts down cleanly on SIGTERM, logs to the journal, and restarts after a crash with a 30-second delay.

```bash
sudo useradd --system --home-dir /var/lib/quant-intelligence --shell /usr/sbin/nologin quant-intelligence
sudo install -d -m 0750 /etc/quant-intelligence
sudo install -m 0600 /path/to/your/quant-intelligence.env /etc/quant-intelligence/quant-intelligence.env
sudo install -m 0644 deploy/quant-intelligence.service /etc/systemd/system/quant-intelligence.service
sudo systemctl daemon-reload
sudo systemctl enable --now quant-intelligence.service
```

Keep `QI_AUDIT_DIR=/var/lib/quant-intelligence/alpaca_audit` for this setup, and make sure `quant-intelligence` is on the service's `PATH`.

I run it on WSL. That works, but if Windows sleeps or shuts down, the trader stops with it, so for real uptime a normal Linux box or a container is better. Nothing in the code is WSL-specific.

### Checking on it

These commands only read from the database. They never contact Alpaca or place orders:

```bash
quant-intelligence trading-status --json        # is it healthy right now?
quant-intelligence trading-activity --limit 25  # what happened recently?
quant-intelligence trading-soak-summary         # the totals: cycles, orders, fills, halts
```

Add `--since` / `--until` (UTC timestamps) to the summary to look at a specific window. For the service itself, use `systemctl status quant-intelligence` and `journalctl -u quant-intelligence -f`.

### When something goes wrong

If health shows `UNKNOWN`, a halt appears, an order stays unresolved, or the database or lock is unavailable, stop and look before doing anything else. Check the broker and the database yourself. Don't force orders, and don't look for a bypass, because there isn't one.

- A **daily halt** clears on its own after a clean reconciliation on the next trading day.
- A **critical halt** (the 30% drawdown stop) needs a person to reset it with `quant-intelligence alpaca-risk-reset`. The reset is logged, and it only works after reconciliation.

To test restarts on purpose, restart the service (`sudo systemctl restart quant-intelligence`), then confirm it started up, took the lock, reconciled, and did **not** run a second cycle for a day it already handled.

### What counts as a successful soak

Profit isn't part of it. The soak passes when several trading days, with restarts mixed in, show:

- no duplicate decisions or orders
- clean restarts and lock handling
- risk state that survives restarts
- every reconciliation and risk event explained
- every order traceable from decision to fill
- enough in the status and activity logs to reconstruct what happened

## Assumptions and limits

Backtests are only as honest as their assumptions, so here are mine:

- **No peeking.** Today's signal uses only data through yesterday's close. Trades fill at today's open.
- **Whole shares only**, no shorting, no leverage, one asset at a time.
- **Costs** are a flat basis-point charge on each trade. There's no modeling of slippage, spreads, or partial fills.
- **Prices are used as given.** The code doesn't adjust for splits or dividends, so it's on you to know whether your data is adjusted.
- **Metrics** assume 252 trading days a year and a 0% risk-free rate for Sharpe. If there isn't enough data for a metric to mean anything, it reports `null` instead of a made-up number.

The full details are in [`METHODOLOGY.md`](METHODOLOGY.md).

On the roadmap: better strategies (v0.9), richer cost and execution models, multiple assets, data sources that say whether prices are adjusted, and eventually the AI research layer that gives the project its name.

## Project layout

```text
src/quant_intelligence/
  data/          loading and validating price data
  strategies/    trading rules (just the SMA trend rule for now)
  backtest/      the backtest engine
  portfolio/     transaction costs
  metrics/       returns, volatility, Sharpe, drawdown
  experiments/   saving and loading backtest results
  trading/       paper trader: broker adapters, risk gate, reconciliation, scheduler, service
  api/           FastAPI app behind the dashboard
frontend/        React + Vite dashboard
alembic/         database migrations
deploy/          systemd unit
tests/           unit and integration tests
```

## Disclaimer

Backtest results are hypothetical and don't represent real trading. Nothing here is financial advice, and none of the results should be read as proof that a strategy makes money.
