import json
from dataclasses import asdict
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.models import TradingDecision
from quant_intelligence.trading.status import OperationalStatus, StatusStore

router = APIRouter(prefix="/api/trader", tags=["trader"])


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _status_store(request: Request) -> StatusStore:
    return request.app.state.trader_status_store


def _audit_store(request: Request) -> TradingAuditStore:
    return request.app.state.trader_audit_store


def _broker(request: Request) -> PaperBroker:
    return PaperBroker(0, state_path=request.app.state.trader_broker_state_path)


def _load_status(request: Request) -> OperationalStatus | None:
    try:
        path = _status_store(request).path
        return _status_store(request).load() if path.is_file() else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"trader status unavailable: {exc}") from exc


def _decision_summary(decision: TradingDecision) -> dict[str, Any]:
    order = decision.submitted_order or decision.proposed_order
    return _json_value({
        "cycle_id": decision.cycle_id,
        "timestamp": decision.timestamp,
        "symbol": decision.symbol,
        "signal": decision.signal,
        "outcome": decision.outcome,
        "risk": {
            "approved": decision.risk_decision.approved,
            "reason": decision.risk_decision.reason,
        },
        "order": {
            "side": order.intent.side,
            "quantity": order.intent.quantity,
            "status": decision.submitted_order.status if decision.submitted_order else None,
        } if order else None,
        "fill": {
            "price": decision.fill.price,
            "quantity": decision.fill.quantity,
            "transaction_cost": decision.fill.transaction_cost,
        } if decision.fill else None,
        "error": decision.error,
    })


@router.get("/status")
def get_trader_status(request: Request) -> dict[str, Any]:
    current = _load_status(request)
    if current is None:
        return {"available": False, "mode": "paper", "reason": "no operational status has been persisted"}
    return {"available": True, "mode": "paper", **_json_value(asdict(current))}


@router.get("/portfolio")
def get_trader_portfolio(request: Request) -> dict[str, Any]:
    current = _load_status(request)
    if current is None:
        return {"available": False, "mode": "paper", "reason": "no operational status has been persisted", "positions": []}
    try:
        broker = _broker(request)
        cash, transaction_costs_paid = broker.get_account()
        positions = broker.get_positions()
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"paper portfolio unavailable: {exc}") from exc
    return _json_value({
        "available": True,
        "mode": "paper",
        "cash": current.current_cash if current.current_cash is not None else cash,
        "equity": current.current_equity,
        "transaction_costs_paid": transaction_costs_paid,
        "positions": [
            {"symbol": position.symbol, "quantity": position.shares, "average_price": position.average_price, "market_price": None, "market_value": None}
            for position in positions
        ],
        "market_data_timestamp": current.most_recent_market_data_timestamp,
        "valuation_note": "Current market price and market value are omitted because they are not persisted by the paper broker.",
    })


@router.get("/decisions")
def list_trader_decisions(request: Request, limit: int = Query(default=25, ge=1, le=100)) -> list[dict[str, Any]]:
    store = _audit_store(request)
    decisions: list[TradingDecision] = []
    for path in store.root.glob("*.json"):
        try:
            decision = store.get(path.stem)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        if decision is not None:
            decisions.append(decision)
    decisions.sort(key=lambda item: item.timestamp, reverse=True)
    return [_decision_summary(decision) for decision in decisions[:limit]]


@router.get("/decisions/{cycle_id}")
def get_trader_decision(cycle_id: str, request: Request) -> dict[str, Any]:
    try:
        decision = _audit_store(request).get(cycle_id)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"decision record unavailable: {exc}") from exc
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="decision not found")
    return _json_value(asdict(decision))
