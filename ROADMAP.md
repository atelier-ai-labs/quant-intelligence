# Quant Intelligence roadmap

**Last reviewed:** 2026-11-20. This file follows the Atelier AI Labs roadmap standard (v1.0).
[docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) remains the detailed engineering state, and
[METHODOLOGY.md](METHODOLOGY.md) explains the backtest math.

## Vision

Quant Intelligence is a sandbox for testing trading ideas honestly. You give it an idea, it backtests the idea,
shows the evidence, then trades it on a paper account so you can see how it behaves in the real market without
risking real money. It's Experiment 002 at Atelier AI. The long-term goal is an AI research assistant that
comes up with hypotheses, tests them, pokes holes in them and explains the results. Before any of that can be
trusted, the boring parts have to be right: the math, the timing, the record-keeping and the safety rails.

**Ground rules for every item:**
- No real money. Live trading stays off unless someone deliberately and explicitly turns it on.
- Alpaca is the source of truth for orders, fills and positions. When in doubt, don't trade.
- Strategies can't place orders; only the risk and execution layers can.
- Anything touching trading, risk, accounting or persistence needs tests.
- Trading execution and risk controls are outside Atelier HQ's authority. Changes there need the owner's
  approval and the safety-review path.

## Current state

- Current milestone: v0.8 forward PAPER soak.
- Working today: the daily-bar backtester (one asset, long-or-cash, whole shares, flat costs), a supervised
  paper trader on Alpaca with a deterministic strategy and risk gate, and all trader state in PostgreSQL (Neon)
  so restarts never send an order twice.
- Durable risk controls: 20% single-position cap, 100% gross exposure cap, no leverage, 5% daily loss breaker,
  30% drawdown halt.
- The service has run since 2026-08-21. As of 2026-11-17 it had logged 43 daily cycles with no duplicate
  orders, no unresolved orders, no reconciliation problems and no risk halts. Every decision so far has been
  HOLD.
- The restart blocker in PROJECT_STATE.md (terminal orders rebuilt as pending) has a fix on main (020c9ce,
  "harden paper restart reconciliation"). Whether it was reviewed and covered by the regression tests is
  **(unverified)**.
- The running service still holds Robinhood shadow-mode code that was discarded on 2026-10-14; it hasn't been
  restarted since. A read-only Robinhood adapter is on main but isn't wired into anything.
- Unmerged research branches from late September (`eng/edgar-ollama-signals`, `eng/event-study-backtest`,
  `eng/expand-event-sample`, `eng/signal-redesign-v2`) hold EDGAR risk-factor signal work. The last event-study
  summary there says NO-GO (n=11) **(unverified)**.
- Next major objective: finish the soak, then v0.9 (smarter strategies).

## Roadmap

### v0.8 — Forward PAPER soak

#### QI-001 — Finish the v0.8 PAPER soak

- **Status:** in_progress
- **Priority:** P1
- **Goal:** Collect soak evidence across more sessions and deliberate restarts, proving the paper trader's
  plumbing is reliable.

**Why it matters:** Nothing smarter can be trusted until the record-keeping and safety rails are proven. v0.9
waits on this.

**Requirements:**
- Several trading sessions, with deliberate restarts mixed in.
- The restart includes the current code, so the discarded Robinhood shadow-mode code is no longer running.

**Done when:**
- No duplicate cycle or order after a restart or retry.
- Startup reconciliation works after a deliberate restart, and the advisory lock behaves correctly.
- Durable risk state survives the restart.
- No unexplained unresolved orders; every reconciliation and risk event is explained.
- Every PAPER submission is traceable decision → risk → submission → fill/state.
- An operator can reconstruct what happened from `trading-status`, `trading-activity` and
  `trading-soak-summary`.
- Profitability is not a criterion.

**Out of scope:** Strategy changes, risk-limit changes, live-money execution.

**References:** docs/PROJECT_STATE.md (PAPER soak acceptance), README.md (What counts as a successful soak)

### v0.9 — Smarter strategies

#### QI-002 — v0.9 strategy system

- **Status:** planned
- **Priority:** P2
- **Goal:** Move beyond the single moving-average trend rule to a strategy system that can hold smarter
  strategies.

**Out of scope:** Live-money execution. Loosening the risk gate.

**Depends on:** QI-001

**References:** docs/PROJECT_STATE.md (Next, step 5)

## Future ideas

- Richer cost and execution models (slippage, spreads, partial fills).
- Multiple assets instead of one at a time.
- Data sources that say whether prices are split- and dividend-adjusted.
- The AI research layer: hypotheses, tests, challenges and explanations from reproducible evidence.
- EDGAR risk-factor signals from the unmerged September research branches.
