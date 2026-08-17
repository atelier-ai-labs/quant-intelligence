import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from quant_intelligence import __version__
from quant_intelligence.backtest import BacktestResult
from quant_intelligence.models import EquityPoint, PortfolioState, StrategySpec, Trade

SCHEMA_VERSION = "1.1"

def _metadata(result: BacktestResult, source_data_sha256: str | None, experiment_id: str | None, created_at: str | None) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "package_version": __version__, "strategy_implementation": "quant_intelligence.strategies.SmaTrendStrategy", "source_data_sha256": source_data_sha256, "benchmark": result.specification.benchmark, "experiment_id": experiment_id, "created_at": created_at or datetime.now(timezone.utc).isoformat()}

def save_result(result: BacktestResult, path: str | Path, source_data: bytes | None = None, experiment_id: str | None = None, created_at: str | None = None) -> Path:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["specification"]["start"] = result.specification.start.isoformat() if result.specification.start else None
    payload["specification"]["end"] = result.specification.end.isoformat() if result.specification.end else None
    payload["metadata"] = _metadata(result, hashlib.sha256(source_data).hexdigest() if source_data is not None else None, experiment_id, created_at)
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return target

def load_result(path: str | Path) -> BacktestResult:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    spec_data = payload["specification"]
    spec = StrategySpec(**{**spec_data, "start": date.fromisoformat(spec_data["start"]) if spec_data.get("start") else None, "end": date.fromisoformat(spec_data["end"]) if spec_data.get("end") else None})
    states = [PortfolioState(date.fromisoformat(item["date"]), item["cash"], item["shares"], item["asset_value"], item["equity"], item["transaction_costs_paid"], item["exposure"], item["desired_position"]) for item in payload["states"]]
    trades = [Trade(date.fromisoformat(item["date"]), item["symbol"], item["side"], item["quantity"], item["execution_price"], item["gross_notional"], item["transaction_cost"], item["cash_after"], item["equity_after"], item["reason"]) for item in payload["trades"]]
    benchmark_equity = [EquityPoint(date.fromisoformat(item["date"]), item["equity"]) for item in payload.get("benchmark_equity", [])]
    return BacktestResult(spec, payload["actual_start"], payload["actual_end"], states, trades, payload["metrics"], payload["benchmark_metrics"], benchmark_equity)
