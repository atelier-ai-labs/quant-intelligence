import json
from dataclasses import asdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import Fill, Order, OrderIntent, PortfolioSnapshot, Position, RiskDecision, SignalAction, TradingDecision

def _json_default(value: Any):
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, StrEnum): return value.value
    raise TypeError(f"cannot serialize {type(value)!r}")

def _intent(data: dict[str, Any] | None) -> OrderIntent | None:
    return None if data is None else OrderIntent(data["symbol"], SignalAction(data["side"]), data["quantity"], data["order_type"], data["asset_type"], data["reason"])

def _snapshot(data: dict[str, Any] | None) -> PortfolioSnapshot | None:
    if data is None: return None
    return PortfolioSnapshot(datetime.fromisoformat(data["timestamp"]), data["cash"], tuple(Position(item["symbol"], item["shares"], item["average_price"]) for item in data["positions"]), data["asset_value"], data["equity"], data["transaction_costs_paid"])

def decision_from_dict(data: dict[str, Any]) -> TradingDecision:
    submitted = data.get("submitted_order")
    order = None if submitted is None else Order(submitted["order_id"], _intent(submitted["intent"]), datetime.fromisoformat(submitted["submitted_at"]), submitted["status"])
    fill_data = data.get("fill")
    fill = None if fill_data is None else Fill(fill_data["order_id"], fill_data["symbol"], SignalAction(fill_data["side"]), fill_data["quantity"], fill_data["price"], fill_data["gross_notional"], fill_data["transaction_cost"], datetime.fromisoformat(fill_data["filled_at"]))
    risk = data["risk_decision"]
    return TradingDecision(data["cycle_id"], datetime.fromisoformat(data["timestamp"]), data["symbol"], data["strategy"], data["strategy_parameters"], datetime.fromisoformat(data["data_timestamp"]) if data.get("data_timestamp") else None, SignalAction(data["signal"]), data["signal_reason"], _snapshot(data.get("portfolio_before")), _intent(data.get("proposed_order")), RiskDecision(risk["approved"], risk["reason"], _intent(risk.get("intent"))), order, fill, _snapshot(data.get("portfolio_after")), data["outcome"], data.get("error"))

class TradingAuditStore:
    def __init__(self, root: str | Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, cycle_id: str) -> Path:
        return self.root / f"{cycle_id}.json"

    def get(self, cycle_id: str) -> TradingDecision | None:
        path = self.path_for(cycle_id)
        if not path.is_file(): return None
        return decision_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, decision: TradingDecision) -> Path:
        path = self.path_for(decision.cycle_id)
        path.write_text(json.dumps(asdict(decision), default=_json_default, indent=2), encoding="utf-8")
        return path
