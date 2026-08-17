import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from quant_intelligence.backtest import run_backtest
from quant_intelligence.data.csv import load_csv
from quant_intelligence.experiments import save_result
from quant_intelligence.models import StrategySpec
from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading import PaperBroker, TradingCycleService
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.risk import RiskGate

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
    args = parser.parse_args()
    if args.command == "backtest":
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
