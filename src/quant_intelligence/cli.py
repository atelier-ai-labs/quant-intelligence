import argparse
import json
from datetime import date
from pathlib import Path
from quant_intelligence.backtest import run_backtest
from quant_intelligence.data.csv import load_csv
from quant_intelligence.experiments import save_result
from quant_intelligence.models import StrategySpec
from quant_intelligence.strategies import SmaTrendStrategy

def main() -> None:
    parser = argparse.ArgumentParser(prog="quant-intelligence")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("backtest")
    p.add_argument("--data", required=True, help="CSV with date,open,high,low,close,volume")
    p.add_argument("--symbol", default="SPY"); p.add_argument("--window", type=int, default=200)
    p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--initial-capital", type=float, default=10_000)
    p.add_argument("--transaction-cost-bps", type=float, default=5); p.add_argument("--output", default="experiments/latest.json")
    args = parser.parse_args()
    if args.command == "backtest":
        spec = StrategySpec("sma-trend", args.symbol, date.fromisoformat(args.start) if args.start else None, date.fromisoformat(args.end) if args.end else None, args.initial_capital, signal_parameters={"window": args.window}, transaction_cost_bps=args.transaction_cost_bps)
        result = run_backtest(load_csv(args.data), spec, SmaTrendStrategy(args.window)); save_result(result, args.output, source_data=Path(args.data).read_bytes())
        summary = {key: result.metrics.get(key) for key in ("total_return", "cagr", "sharpe_ratio", "maximum_drawdown", "number_of_trades", "transaction_costs_paid")}
        summary["benchmark_return"] = result.benchmark_metrics["total_return"]; summary["ending_equity"] = result.states[-1].equity
        print(json.dumps({"date_range": [result.actual_start, result.actual_end], "starting_capital": spec.initial_capital, **summary}, indent=2))
