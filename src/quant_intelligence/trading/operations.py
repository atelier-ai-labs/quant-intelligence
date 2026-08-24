"""Read-only operator views over durable trading state.

This module deliberately contains presentation-oriented projections only. It
does not call a broker, evaluate a strategy, or mutate trading state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _decision_view(decision: Any) -> dict[str, Any]:
    return {
        "cycle_id": decision.cycle_id,
        "timestamp": _json(decision.timestamp),
        "data_timestamp": _json(decision.data_timestamp),
        "symbol": decision.symbol,
        "strategy": decision.strategy,
        "signal": _json(decision.signal),
        "signal_reason": decision.signal_reason,
        "outcome": decision.outcome,
        "risk": _json(decision.risk_decision),
        "proposed_order": _json(decision.proposed_order),
        "submitted_order": _json(decision.submitted_order),
        "fill": _json(decision.fill),
        "reconciliation": _json(decision.reconciliation),
        "error": decision.error,
    }


def status_view(repository: Any, *, configured_symbols: tuple[str, ...] = ()) -> dict[str, Any]:
    status = repository.load_status()
    risk_state = repository.load_risk_state() if hasattr(repository, "load_risk_state") else None
    unresolved = repository.pending_reconciliations() if hasattr(repository, "pending_reconciliations") else []
    recent_reconciliations = repository.list_reconciliations(1) if hasattr(repository, "list_reconciliations") else []
    last_reconciliation = recent_reconciliations[0] if recent_reconciliations else None
    health = status.trading_health if status.trading_health in {"healthy", "halted", "unknown"} else "unknown"
    risk_mode = risk_state.mode.value if risk_state is not None else (status.risk_mode if status.risk_mode != "NORMAL" else "UNKNOWN")
    if risk_mode not in {"NORMAL", "DAILY_HALT", "CRITICAL_HALT"}:
        risk_mode = "UNKNOWN"
    return _json({
        "service_state": status.state,
        "mode": status.mode,
        "execution_enabled": status.execution_enabled,
        "configured_symbols": list(configured_symbols) or list(status.managed_symbols),
        "latest_completed_session": status.latest_completed_session,
        "latest_cycle_id": status.last_cycle_id,
        "latest_cycle_timestamp": status.last_cycle_timestamp,
        "latest_cycle_outcome": status.last_cycle_outcome,
        "next_decision": status.next_scheduled_decision,
        "trading_health": health.upper(),
        "broker_health": status.broker_health.upper() if status.broker_health else "UNKNOWN",
        "market_data_health": status.market_data_health.upper() if status.market_data_health else "UNKNOWN",
        "halt_reason": status.halt_reason,
        "risk_mode": risk_mode,
        "daily_baseline_equity": risk_state.daily_baseline_equity if risk_state else None,
        "high_water_equity": risk_state.high_water_equity if risk_state else None,
        "critical_latched": risk_state.critical_latched if risk_state else None,
        "current_equity": status.current_equity,
        "current_cash": status.current_cash,
        "positions": [{"symbol": p.symbol, "quantity": p.shares, "average_price": p.average_price} for p in status.current_positions],
        "unresolved_order_count": max(status.unresolved_order_count, len(unresolved)),
        "unresolved_orders": unresolved,
        "last_reconciliation": last_reconciliation or status.last_reconciliation_status,
        "last_reconciliation_at": (last_reconciliation or {}).get("reconciled_at") if last_reconciliation else status.last_reconciliation_timestamp,
        "last_error": status.last_error,
        "updated_at": getattr(status, "updated_at", None),
    })


def activity_view(repository: Any, limit: int = 25) -> list[dict[str, Any]]:
    """Return bounded newest-first activity without performing any writes."""
    decisions = [_decision_view(item) for item in repository.list()[:max(1, limit)]]
    records: list[dict[str, Any]] = [{"kind": "cycle", "timestamp": item["timestamp"], "record": item} for item in decisions]
    for method, kind in (("list_orders", "order"), ("list_fills", "fill"), ("list_reconciliations", "reconciliation"), ("list_risk_events", "risk"), ("list_operational_events", "lifecycle")):
        if hasattr(repository, method):
            records.extend({"kind": kind, "timestamp": _json(item.get("updated_at") or item.get("filled_at") or item.get("reconciled_at") or item.get("created_at")), "record": _json(item)} for item in getattr(repository, method)(limit))
    return sorted(records, key=lambda item: item["timestamp"] or "", reverse=True)[:max(1, limit)]


def soak_summary(repository: Any, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    decisions = repository.list()
    def comparable(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    def in_range(value: datetime | None) -> bool:
        return value is not None and (since is None or comparable(value) >= comparable(since)) and (until is None or comparable(value) <= comparable(until))
    selected = [item for item in decisions if in_range(item.timestamp)] if since or until else decisions
    outcomes = Counter(item.outcome for item in selected)
    signals = Counter(_json(item.signal) for item in selected)
    orders = getattr(repository, "list_orders", lambda limit=None: [])(None)
    fills = getattr(repository, "list_fills", lambda limit=None: [])(None)
    reconciliations = getattr(repository, "list_reconciliations", lambda limit=None: [])(None)
    risk_events = getattr(repository, "list_risk_events", lambda limit=None: [])(None)
    if since or until:
        orders = [item for item in orders if in_range(item.get("updated_at") or item.get("submitted_at"))]
        fills = [item for item in fills if in_range(item.get("filled_at"))]
        reconciliations = [item for item in reconciliations if in_range(item.get("reconciled_at"))]
        risk_events = [item for item in risk_events if in_range(item.get("created_at"))]
    sessions = [item.data_timestamp.date().isoformat() for item in selected if item.data_timestamp]
    cycle_identities = [(item.strategy, str(item.strategy_parameters.get("version", "1")), item.symbol, session, "observation" if item.outcome.startswith("WOULD") else "paper_execution") for item, session in ((item, item.data_timestamp.date().isoformat() if item.data_timestamp else None) for item in selected) if session]
    duplicate_violations = sum(count - 1 for count in Counter(cycle_identities).values() if count > 1)
    return _json({
        "period": {"since": since, "until": until},
        "cycles": len(selected),
        "signals": dict(signals),
        "outcomes": dict(outcomes),
        "orders_proposed": sum(item.proposed_order is not None for item in selected),
        "orders_submitted": len(orders),
        "fills_observed": len(fills),
        "unresolved_orders": sum(item.get("status") not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"} for item in orders),
        "reconciliation_anomalies": sum(item.get("status") not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "MATCH"} for item in reconciliations),
        "reconciliations": len(reconciliations),
        "risk_rejections": sum(not item.risk_decision.approved for item in selected),
        "daily_breaker_triggers": sum(item.get("event_type") == "DAILY_HALT" for item in risk_events),
        "critical_halt_triggers": sum(item.get("event_type") == "CRITICAL_HALT" for item in risk_events),
        "duplicate_cycle_violations": duplicate_violations,
        "earliest_session": min(sessions) if sessions else None,
        "latest_session": max(sessions) if sessions else None,
        "risk_events_observed": len(risk_events),
    })
