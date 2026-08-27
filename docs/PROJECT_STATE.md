# Quant Intelligence — Canonical Project State

This document is the canonical current state of Quant Intelligence. If it conflicts with model memory, old chat context, stale documentation, or older repository history, this document wins.

Last updated: 2026-08-26T22:03:11-04:00
Current git commit: `4551a34c0f7a4da29450b5fe579654862d93a3f7`

## Current milestone

**v0.8 forward PAPER soak hardening**

## Completed

- v0.5 PostgreSQL / Neon operational persistence
- SQLAlchemy + psycopg + Alembic
- v0.6 supervised persistent runtime
- PostgreSQL advisory lock / single-service ownership
- Startup reconciliation and restart-safe/idempotent service behavior
- v0.7 durable risk controls: 20% single-position exposure cap, 100% gross exposure cap, no leverage/margin, 5% daily equity-loss breaker, and 30% high-water drawdown critical halt
- Durable risk state and structured risk events
- v0.8 soak tooling: `trading-status`, `trading-activity`, and `trading-soak-summary`
- Durable operational lifecycle events
- Authoritative market-session handling and same-day completed-session market-data fix
- Mode-aware cycle identity for observation vs PAPER execution
- PostgreSQL-backed dry-run/observation persistence; dry-run no longer silently switches to JSON
- Retrospective Terra safety audit

## Current architecture

- Python monolith with PostgreSQL / Neon as authoritative durable QI operational state
- SQLAlchemy + Alembic; Alpaca PAPER broker execution
- Supervised `alpaca-service`
- Deterministic strategy and deterministic `RiskGate`
- Alpaca authoritative for broker execution, orders, fills, and positions; QI authoritative for strategy intent and cycle decisions
- Fail closed on uncertain execution or reconciliation state
- Live-money execution remains disabled

Architectural principle: Build single-user today without making single-user assumptions irreversible, so future product/subscription use remains possible without premature SaaS infrastructure.

## Current blocker

PAPER soak is paused while restart/reconciliation hardening is completed. The original expected-position reconciliation bug was fixed: filled autonomous orders must agree with the expected post-trade broker position before unresolved state clears.

Terra then found a remaining restart edge case: historical terminal orders in `alpaca_orders.json` can still be reconstructed as pending metadata and falsely block a later session. Required behavior is that terminal successfully reconciled orders are not pending, while genuine unresolved mismatches remain durable/recoverable even when broker order state is terminal. Fix and review this blocker before resuming PAPER soak.

## Next

1. Finish the terminal-order pending-metadata/restart fix.
2. Review it and add regression coverage.
3. Resume multi-session PAPER soak.
4. Collect soak evidence across several sessions/restarts.
5. After successful soak, begin v0.9 strategy system / smarter strategies.

## PAPER soak acceptance

- No duplicate cycle/order after restart or retry
- Startup reconciliation works after deliberate restart
- Advisory lock behaves correctly
- Durable risk state survives restart
- No unexplained unresolved orders
- Every PAPER submission is traceable decision → risk → submission → fill/state
- Clean shutdown/restart
- An operator can reconstruct behavior from status/activity/soak-summary
- Profitability is **not** a v0.8 acceptance criterion

## Critical invariants

- Broker execution truth comes from Alpaca; strategy intent and cycle decisions come from QI.
- Reconciliation precedes autonomous strategy execution; uncertainty fails closed and unresolved mismatches block new exposure.
- Risk controls are durable and enforce the stated exposure, leverage, loss, and drawdown limits.
- Cycle identity and persistence are mode-aware, durable, restart-safe, and idempotent.
- Live-money execution cannot be enabled implicitly.

## Deprecated / not current architecture

- DeepSeek Harness is no longer part of QI architecture.
- JSON is not normal supervised execution persistence.
- The old Sol-as-Codex-orchestrator workflow is no longer current.
- The old “slim API is next” roadmap is obsolete.
- QI is not currently at the 36-test / early frontend stage.

## Current development workflow

- ChatGPT Sol: architecture, direction, prioritization
- Codex Luna: implementation and debugging
- Terra / Gauss role: independent safety review for high-risk changes
- Fast path for ordinary changes; safety path when blast radius warrants it
