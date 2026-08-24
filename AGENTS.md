# Quant Intelligence engineering rules

- Alpaca is authoritative for broker-side order, fill, and position state. Quant Intelligence preserves the evidence and reconciles its local mirror; it must not treat local state as a substitute for broker truth.
- PostgreSQL is the durable persistence layer for autonomous operational state when PostgreSQL mode is enabled. Research and backtest artifacts remain file-based during the current transition.
- Strategies express intent; risk, execution, and broker layers own order authorization and submission. Strategy code must not submit broker orders directly.
- Reconciliation occurs before autonomous strategy execution. Unresolved or untrusted broker state must block new exposure and must not create duplicate orders.
- Broker, market-data, reconciliation, calendar, or required-persistence uncertainty is fail-closed: halt or record no-trade rather than guessing or continuing execution.
- Research and backtest financial calculations remain backend-owned; presentation layers may format and display authoritative results but must not reimplement them.
- Changes to trading behavior, risk controls, accounting, persistence, idempotency, or migrations require deterministic tests and regression verification.
- Live-money execution must remain disabled by default and may not be enabled casually or implicitly.
