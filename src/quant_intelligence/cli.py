import argparse
import json
import logging
import os
import signal
import time
from threading import Event
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from quant_intelligence.backtest import run_backtest
from quant_intelligence.data.csv import load_csv
from quant_intelligence.experiments import save_result
from quant_intelligence.models import StrategySpec
from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading import AutonomousTrader, IntervalScheduler, PaperBroker, StatusStore, SystemClock, TradingCycleService
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.risk import RiskConfig, RiskGate
from quant_intelligence.trading.alpaca import AlpacaBroker, AlpacaConfig, AlpacaConfigurationError, AlpacaMarketDataProvider
from quant_intelligence.trading.broker import BrokerError
from quant_intelligence.trading.reconciliation import BrokerReconciliation
from quant_intelligence.trading.sessions import AlpacaMarketSessionProvider
from quant_intelligence.trading.persistence import PersistenceConfigurationError, operational_repository
from quant_intelligence.trading.operations import activity_view, soak_summary, status_view
from quant_intelligence.trading.runtime import CriticalResetError, CriticalRiskResetService, RuntimeConfigurationError, ServiceAlreadyRunning, ServiceConfig, build_persistent_service

def main() -> None:
    parser = argparse.ArgumentParser(prog="quant-intelligence")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("backtest")
    p.add_argument("--data", required=True, help="CSV with date,open,high,low,close,volume")
    p.add_argument("--symbol", default="SPY"); p.add_argument("--window", type=int, default=200)
    p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--initial-capital", type=float, default=10_000)
    p.add_argument("--transaction-cost-bps", type=float, default=5); p.add_argument("--output", default="experiments/latest.json")
    c = sub.add_parser("paper-cycle")
    c.add_argument("--data", required=True, help="CSV with date,open,high,low,close,volume")
    c.add_argument("--symbol", default="SPY"); c.add_argument("--window", type=int, default=200)
    c.add_argument("--initial-capital", type=float, default=10_000); c.add_argument("--transaction-cost-bps", type=float, default=5)
    c.add_argument("--audit-dir", default="paper_audit"); c.add_argument("--timestamp", help="UTC ISO timestamp for deterministic runs")
    r = sub.add_parser("paper-run")
    r.add_argument("--data", required=True, help="CSV with date,open,high,low,close,volume")
    r.add_argument("--symbol", default="SPY"); r.add_argument("--window", type=int, default=200)
    r.add_argument("--initial-capital", type=float, default=10_000); r.add_argument("--transaction-cost-bps", type=float, default=5)
    r.add_argument("--audit-dir", default="paper_audit"); r.add_argument("--interval-seconds", type=float, default=86400); r.add_argument("--cycles", type=int, default=1, help="finite number of scheduled cycles; defaults to one")
    d = sub.add_parser("alpaca-data-check")
    d.add_argument("--symbol", default="SPY"); d.add_argument("--window", type=int, default=200)
    a = sub.add_parser("alpaca-cycle")
    a.add_argument("--symbol", default="SPY"); a.add_argument("--window", type=int, default=200); a.add_argument("--audit-dir", default="alpaca_audit"); a.add_argument("--max-order-shares", type=int, help="explicit execution cap, intended for the one-share smoke test"); a.add_argument("--poll-attempts", type=int, default=5); a.add_argument("--poll-seconds", type=float, default=2.0)
    mode = a.add_mutually_exclusive_group(); mode.add_argument("--observe", action="store_true", help="observation-only mode; never submits an order"); mode.add_argument("--execute", action="store_true", help="submit only to explicitly configured Alpaca paper account")
    ar = sub.add_parser("alpaca-run", help="run the session-aware autonomous Alpaca paper trader")
    ar.add_argument("--symbol", default="SPY"); ar.add_argument("--window", type=int, default=200); ar.add_argument("--audit-dir", default="alpaca_audit")
    ar.add_argument("--post-close-delay-minutes", type=float, default=5.0); ar.add_argument("--max-position-allocation", type=float, default=0.20)
    ar.add_argument("--max-order-notional", type=float, default=2500.0); ar.add_argument("--max-order-shares", type=int, default=10)
    ar.add_argument("--dry-run-once", action="store_true", help="reconcile and show the next/current decision without submitting")
    sub.add_parser("alpaca-service", help="run the environment-configured supervised Alpaca paper trader")
    reset = sub.add_parser("alpaca-risk-reset", help="audited critical-risk reset; never submits an order")
    reset.add_argument("--reason", required=True)
    reset.add_argument("--operator", required=True)
    status_cmd = sub.add_parser("trading-status", help="show read-only durable autonomous-trader status")
    status_cmd.add_argument("--json", action="store_true", dest="as_json")
    activity_cmd = sub.add_parser("trading-activity", help="show bounded read-only autonomous activity")
    activity_cmd.add_argument("--limit", type=int, default=25)
    activity_cmd.add_argument("--json", action="store_true", dest="as_json")
    summary_cmd = sub.add_parser("trading-soak-summary", help="summarize bounded read-only soak evidence")
    summary_cmd.add_argument("--since", help="inclusive UTC ISO timestamp")
    summary_cmd.add_argument("--until", help="inclusive UTC ISO timestamp")
    summary_cmd.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command in {"trading-status", "trading-activity", "trading-soak-summary"}:
        repository = None
        try:
            audit_dir = Path(os.getenv("QI_AUDIT_DIR", "alpaca_audit"))
            repository = operational_repository(audit_dir, database_url=os.getenv("DATABASE_URL"), mode=os.getenv("QI_TRADING_PERSISTENCE", "postgres"))
            if args.command == "trading-status":
                report = status_view(repository, configured_symbols=tuple(filter(None, (os.getenv("QI_SYMBOL", "").strip().upper(),))))
            elif args.command == "trading-activity":
                if args.limit < 1 or args.limit > 500:
                    parser.error("--limit must be between 1 and 500")
                report = activity_view(repository, args.limit)
            else:
                def parse_bound(value: str | None) -> datetime | None:
                    if not value:
                        return None
                    try:
                        parsed = datetime.fromisoformat(value)
                    except ValueError as exc:
                        parser.error(f"invalid timestamp: {value}")
                        raise AssertionError from exc
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                report = soak_summary(repository, since=parse_bound(args.since), until=parse_bound(args.until))
            if getattr(args, "as_json", False):
                print(json.dumps(report, indent=2, default=str))
            elif args.command == "trading-status":
                print("QUANT INTELLIGENCE TRADING STATUS")
                for key, value in report.items():
                    if key not in {"positions", "unresolved_orders"}:
                        print(f"{key.replace('_', ' ').title()}: {value}")
                if report["positions"]:
                    print("Positions:")
                    for position in report["positions"]:
                        print(f"  {position['symbol']}: {position['quantity']} @ {position['average_price']}")
                else:
                    print("Positions: none observed")
            else:
                for key, value in report.items():
                    print(f"{key.replace('_', ' ').title()}: {value}")
        except (PersistenceConfigurationError, OSError, RuntimeError) as exc:
            parser.error(f"read-only operational inspection unavailable: {exc}")
        finally:
            if repository is not None and hasattr(repository, "close"):
                repository.close()
    elif args.command == "backtest":
        spec = StrategySpec("sma-trend", args.symbol, date.fromisoformat(args.start) if args.start else None, date.fromisoformat(args.end) if args.end else None, args.initial_capital, signal_parameters={"window": args.window}, transaction_cost_bps=args.transaction_cost_bps)
        result = run_backtest(load_csv(args.data), spec, SmaTrendStrategy(args.window)); save_result(result, args.output, source_data=Path(args.data).read_bytes())
        summary = {key: result.metrics.get(key) for key in ("total_return", "cagr", "sharpe_ratio", "maximum_drawdown", "number_of_trades", "transaction_costs_paid")}
        summary["benchmark_return"] = result.benchmark_metrics["total_return"]; summary["ending_equity"] = result.states[-1].equity
        print(json.dumps({"date_range": [result.actual_start, result.actual_end], "starting_capital": spec.initial_capital, **summary}, indent=2))
    elif args.command == "paper-cycle":
        bars = load_csv(args.data)
        now = datetime.fromisoformat(args.timestamp) if args.timestamp else datetime.now(timezone.utc)
        provider = FixtureMarketDataProvider(args.symbol, bars)
        broker = PaperBroker(args.initial_capital, args.transaction_cost_bps, state_path=Path(args.audit_dir) / "broker_state.json")
        service = TradingCycleService(strategy=SmaTrendStrategy(args.window), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=args.transaction_cost_bps), audit_store=TradingAuditStore(args.audit_dir))
        print(json.dumps(asdict(service.run(args.symbol, now)), default=str, indent=2))
    elif args.command == "paper-run":
        if args.cycles < 1: parser.error("--cycles must be positive")
        bars = load_csv(args.data); clock = SystemClock(); now = clock.now()
        provider = FixtureMarketDataProvider(args.symbol, bars)
        broker = PaperBroker(args.initial_capital, args.transaction_cost_bps, state_path=Path(args.audit_dir) / "broker_state.json")
        service = TradingCycleService(strategy=SmaTrendStrategy(args.window), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=args.transaction_cost_bps), audit_store=TradingAuditStore(args.audit_dir))
        trader = AutonomousTrader(symbol=args.symbol, cycle_service=service, scheduler=IntervalScheduler(timedelta(seconds=args.interval_seconds), now), clock=clock, status_store=StatusStore(Path(args.audit_dir) / "status.json"))
        trader.start(); completed = 0
        try:
            while completed < args.cycles:
                decisions = trader.run_due_cycles()
                completed += len(decisions)
                if completed < args.cycles: time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            logger = logging.getLogger(__name__); logger.info("shutdown_requested")
        finally:
            trader.stop()
    elif args.command == "alpaca-data-check":
        try:
            config = AlpacaConfig.from_env()
        except AlpacaConfigurationError as exc:
            parser.error(str(exc))
        provider = AlpacaMarketDataProvider(args.symbol, args.window, config)
        snapshot = provider.get_completed_bars(args.symbol, datetime.now(timezone.utc))
        print(json.dumps({"symbol": snapshot.symbol, "bars": len(snapshot.bars), "first_completed_date": snapshot.bars[0].date.isoformat(), "last_completed_date": snapshot.bars[-1].date.isoformat(), "last_close": snapshot.latest_price, "data_timestamp": snapshot.data_timestamp.isoformat(), "feed": config.data_feed}, indent=2))
    elif args.command == "alpaca-cycle":
        try:
            config = AlpacaConfig.from_env()
        except AlpacaConfigurationError as exc:
            parser.error(str(exc))
        if args.execute and not config.execution_enabled:
            parser.error("--execute requires APCA_EXECUTION_ENABLED=true; observation is the default")
        execution_config = replace(config, execution_enabled=bool(args.execute and config.execution_enabled))
        now = datetime.now(timezone.utc)
        provider = AlpacaMarketDataProvider(args.symbol, args.window, execution_config)
        broker = AlpacaBroker(execution_config, state_path=Path(args.audit_dir) / "alpaca_orders.json")
        try:
            repository = operational_repository(args.audit_dir, database_url=os.getenv("DATABASE_URL") if args.execute else None, mode="postgres" if args.execute else "json")
        except PersistenceConfigurationError as exc:
            parser.error(f"Alpaca execution requires PostgreSQL operational persistence: {exc}")
        if args.max_order_shares is not None and args.max_order_shares < 1:
            parser.error("--max-order-shares must be positive")
        if args.poll_attempts < 1 or args.poll_seconds < 0:
            parser.error("poll attempts must be positive and poll seconds cannot be negative")
        if args.execute:
            try:
                broker.preflight(args.symbol)
            except BrokerError as exc:
                parser.error(f"Alpaca preflight refused execution: {exc}")
            print(f"MODE: ALPACA PAPER EXECUTION\nSYMBOL: {args.symbol}\nMAX EXECUTION QUANTITY: {args.max_order_shares or 'strategy-sized'} SHARE(S)\nBROKER: PAPER")
        service = TradingCycleService(strategy=SmaTrendStrategy(args.window), broker=broker, market_data=provider, risk_gate=RiskGate(), audit_store=repository, execution_mode="paper_execution" if args.execute else "observation", max_execution_shares=args.max_order_shares, risk_state_store=repository if args.execute else None)
        decision = service.run(args.symbol, now)
        if args.execute and decision.submitted_order is not None:
            observed_order, observed_fill = broker.poll_order(decision.submitted_order, attempts=args.poll_attempts, interval_seconds=args.poll_seconds)
            outcome = "EXECUTED" if observed_fill is not None and observed_order.status == "FILLED" else observed_order.status
            expected_before = next((position.shares for position in decision.portfolio_before.positions if position.symbol == args.symbol), 0) if decision.portfolio_before else None
            filled_quantity = observed_fill.quantity if observed_fill else 0
            expected_after = None if expected_before is None else expected_before + (filled_quantity if observed_order.intent.side.value == "BUY" else -filled_quantity)
            reconciliation = BrokerReconciliation(broker, Path(args.audit_dir) / "reconciliation", repository=repository).reconcile_order(cycle_id=decision.cycle_id, symbol=args.symbol, client_order_id=decision.submitted_order.client_order_id or "", expected_quantity=decision.execution_order.quantity if decision.execution_order else decision.submitted_order.intent.quantity, expected_position=expected_after)
            decision = replace(decision, submitted_order=observed_order, fill=observed_fill, outcome=outcome, reconciliation=asdict(reconciliation))
            service.audit_store.save(decision)
        status_store = repository
        operational = status_store.load()
        operational.mode = "paper"
        operational.execution_enabled = execution_config.execution_enabled
        operational.broker_connected = True if decision.portfolio_before is not None else None
        operational.update_from_decision(decision)
        status_store.save(operational)
        print(json.dumps({"mode": "paper-execution" if execution_config.execution_enabled else "paper-observation", "cycle_id": decision.cycle_id, "signal": decision.signal, "outcome": decision.outcome, "data_timestamp": decision.data_timestamp, "client_order_id": decision.proposed_order.client_order_id if decision.proposed_order else None, "alpaca_order_id": decision.submitted_order.broker_order_id if decision.submitted_order else None, "order_status": decision.submitted_order.status if decision.submitted_order else None, "filled_quantity": decision.fill.quantity if decision.fill else 0, "actual_fill_price": decision.fill.price if decision.fill else None, "reconciliation": decision.reconciliation}, default=str, indent=2))
    elif args.command == "alpaca-run":
        try:
            config = AlpacaConfig.from_env()
        except AlpacaConfigurationError as exc:
            parser.error(str(exc))
        if not args.dry_run_once and not config.execution_enabled:
            parser.error("alpaca-run requires APCA_EXECUTION_ENABLED=true; use --dry-run-once for validation")
        if args.max_order_shares < 1:
            parser.error("--max-order-shares must be positive")
        if args.max_order_notional <= 0 or not 0 < args.max_position_allocation <= 0.20:
            parser.error("risk limits must be positive and allocation must be in (0, 0.20]")
        audit_dir = Path(args.audit_dir)
        execution_config = replace(config, execution_enabled=bool(config.execution_enabled and not args.dry_run_once))
        provider = AlpacaMarketDataProvider(args.symbol, args.window, execution_config)
        broker = AlpacaBroker(execution_config, state_path=audit_dir / "alpaca_orders.json")
        session_provider = AlpacaMarketSessionProvider(broker.client)
        try:
            repository = operational_repository(audit_dir, database_url=os.getenv("DATABASE_URL") if execution_config.execution_enabled else None, mode="postgres" if execution_config.execution_enabled else "json")
        except PersistenceConfigurationError as exc:
            parser.error(f"Alpaca execution requires PostgreSQL operational persistence: {exc}")
        reconciliation_service = BrokerReconciliation(broker, audit_dir / "reconciliation", repository=repository)
        risk_config = RiskConfig(max_position_allocation=args.max_position_allocation, max_order_notional=args.max_order_notional)
        service = TradingCycleService(strategy=SmaTrendStrategy(args.window), broker=broker, market_data=provider, risk_gate=RiskGate(risk_config), audit_store=repository, execution_mode="paper_execution" if execution_config.execution_enabled else "observation", max_execution_shares=args.max_order_shares, risk_state_store=repository if execution_config.execution_enabled else None, risk_config=risk_config)
        trader = AutonomousTrader(symbol=args.symbol, cycle_service=service, scheduler=None, clock=SystemClock(), status_store=repository, session_provider=session_provider, post_close_delay=timedelta(minutes=args.post_close_delay_minutes), reconciliation_service=reconciliation_service)
        trader.start()
        try:
            if args.dry_run_once:
                next_time = trader.next_decision_time()
                decisions = trader.run_due_cycles()
                print(json.dumps({"mode": "dry-run", "symbol": args.symbol, "latest_completed_session": trader.status.latest_completed_session, "next_decision": next_time, "trading_health": trader.status.trading_health, "halt_reason": trader.status.halt_reason, "decision": asdict(decisions[0]) if decisions else None}, default=str, indent=2))
            else:
                while True:
                    trader.run_due_cycles()
                    if trader.status.unresolved_symbols:
                        time.sleep(60)
                        trader.reconcile_now()
                        continue
                    next_time = trader.next_decision_time()
                    if next_time is None:
                        time.sleep(300)
                    else:
                        delay = max(1.0, (next_time - trader.clock.now()).total_seconds())
                        time.sleep(delay)
        except KeyboardInterrupt:
            logging.getLogger(__name__).info("shutdown_requested")
        finally:
            trader.stop()
    elif args.command == "alpaca-service":
        try:
            service_config = ServiceConfig.from_env()
            stop_event = Event()
            runtime = build_persistent_service(service_config, stop_event=stop_event)
        except RuntimeConfigurationError as exc:
            parser.error(str(exc))

        def request_shutdown(signum, _frame):
            logging.getLogger(__name__).info("termination_signal_received", extra={"signal": signum})
            runtime.request_stop()

        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in previous:
            signal.signal(sig, request_shutdown)
        try:
            runtime.run()
        except ServiceAlreadyRunning as exc:
            logging.getLogger(__name__).error("service_instance_refused", extra={"reason": str(exc)})
            raise SystemExit(1) from exc
        except Exception as exc:
            logging.getLogger(__name__).critical("service_exited_fatally", extra={"error_type": type(exc).__name__})
            raise SystemExit(1) from exc
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    elif args.command == "alpaca-risk-reset":
        if not args.reason.strip() or not args.operator.strip():
            parser.error("--operator and --reason must be non-empty")
        repository = None
        try:
            config = ServiceConfig.from_env()
            repository = operational_repository(config.audit_dir, database_url=config.database_url, mode="postgres")
            broker = AlpacaBroker(config.alpaca, state_path=config.audit_dir / "alpaca_orders.json")
            reconciliation = BrokerReconciliation(broker, config.audit_dir / "reconciliation", repository=repository)
            service = CriticalRiskResetService(repository=repository, broker=broker, reconciliation_service=reconciliation, session_provider=AlpacaMarketSessionProvider(broker.client), symbol=config.symbol, lock_name=config.service_lock_name, clock=SystemClock(), post_close_delay=timedelta(minutes=config.post_close_delay_minutes))
            evaluation = service.reset(operator=args.operator, reason=args.reason)
            print(json.dumps({"mode": evaluation.state.mode.value, "operator": args.operator.strip(), "reason": args.reason.strip(), "orders_submitted": 0}, indent=2))
        except (RuntimeConfigurationError, CriticalResetError, BrokerError, ValueError) as exc:
            parser.error(str(exc))
        finally:
            if repository is not None:
                close = getattr(repository, "close", None)
                if close is not None:
                    close()
