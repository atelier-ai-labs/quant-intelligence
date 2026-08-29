"""Small, evidence-preserving reconciliation service for external brokers."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .broker import BrokerUnavailable


TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
NON_TERMINAL_ORDER_STATUSES = frozenset({"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "UNKNOWN", "PENDING_SUBMISSION"})
RECOVERABLE_RECONCILIATION_STATUSES = frozenset({"MISMATCH", "UNKNOWN", "MISSING", "UNRESOLVED"})


def _position_quantities(portfolio_before: Any) -> dict[str, int] | None:
    if portfolio_before is None:
        return None
    positions = getattr(portfolio_before, "positions", None)
    if positions is None and isinstance(portfolio_before, dict):
        positions = portfolio_before.get("positions")
    if not isinstance(positions, (list, tuple)):
        return None
    quantities: dict[str, int] = {}
    for position in positions:
        symbol = getattr(position, "symbol", None) if not isinstance(position, dict) else position.get("symbol")
        shares = getattr(position, "shares", None) if not isinstance(position, dict) else position.get("shares")
        if not isinstance(symbol, str) or not symbol or not isinstance(shares, int) or isinstance(shares, bool) or shares < 0 or symbol in quantities:
            return None
        quantities[symbol] = shares
    return quantities


def expected_post_trade_position(portfolio_before: Any, symbol: str, side: Any, quantity: int) -> int | None:
    """Derive a durable position target from persisted pre-trade state and intent."""
    if portfolio_before is None or not isinstance(quantity, int) or quantity <= 0:
        return None
    quantities = _position_quantities(portfolio_before)
    if quantities is None:
        return None
    current = quantities.get(symbol, 0)
    action = getattr(side, "value", side)
    if action == "BUY":
        return current + quantity
    if action == "SELL" and quantity <= current:
        return current - quantity
    return None


def pre_trade_position(portfolio_before: Any, symbol: str) -> int | None:
    """Extract the durable pre-trade quantity used for partial/terminal fills."""
    quantities = _position_quantities(portfolio_before)
    if quantities is None:
        return None
    return quantities.get(symbol, 0)


@dataclass(frozen=True)
class ReconciliationResult:
    cycle_id: str
    symbol: str
    client_order_id: str
    status: str
    expected_quantity: int
    observed_filled_quantity: int
    expected_position: int | None
    observed_position: int | None
    broker_order_id: str | None
    last_reconciled_at: datetime
    error: str | None = None


class BrokerReconciliation:
    def __init__(self, broker: Any, root: str | Path, repository: Any | None = None):
        self.broker = broker
        self.root = Path(root)
        self.repository = repository
        self.root.mkdir(parents=True, exist_ok=True)

    def reconcile_order(self, *, cycle_id: str, symbol: str, client_order_id: str, expected_quantity: int, expected_position: int | None = None, pre_trade_position: int | None = None, require_position: bool = False) -> ReconciliationResult:
        now = datetime.now(timezone.utc)
        try:
            order = self.broker.get_order_by_client_id(client_order_id)
            positions = self.broker.get_positions()
        except BrokerUnavailable as exc:
            result = ReconciliationResult(cycle_id, symbol, client_order_id, "UNKNOWN", expected_quantity, 0, expected_position, None, None, now, str(exc))
            self._save(result)
            return result
        observed_position = next((position.shares for position in positions if position.symbol == symbol), 0)
        if order is None:
            result = ReconciliationResult(cycle_id, symbol, client_order_id, "MISSING", expected_quantity, 0, expected_position, observed_position, None, now, "ambiguous order was not found; stale intent will not be replayed")
        else:
            filled = order.filled_quantity
            # A target reconstructed from the full intent is not meaningful
            # for a canceled/rejected order.  For those states, only a
            # durable pre-trade position plus observed fills supplies a
            # post-trade target.
            effective_expected_position = expected_position if order.status == "FILLED" else None
            if pre_trade_position is not None:
                signed_fill = filled if getattr(order.intent.side, "value", order.intent.side) == "BUY" else -filled
                effective_expected_position = pre_trade_position + signed_fill
            position_context_missing = require_position and effective_expected_position is None
            position_mismatch = effective_expected_position is not None and effective_expected_position != observed_position
            filled_quantity_mismatch = order.status == "FILLED" and filled != expected_quantity
            mismatch = (order.status == "FILLED" and (position_context_missing or position_mismatch or filled_quantity_mismatch)) or (order.status != "FILLED" and effective_expected_position is not None and position_mismatch)
            if order.status not in TERMINAL_ORDER_STATUSES:
                status = "UNRESOLVED"
                error = f"broker order remains {order.status}"
            else:
                status = "MISMATCH" if mismatch else order.status
                error = "expected post-trade position is unavailable or disagrees with broker" if mismatch else None
            result = ReconciliationResult(cycle_id, symbol, client_order_id, status, expected_quantity, filled, effective_expected_position, observed_position, order.broker_order_id, now, error)
        self._save(result)
        if result.status in TERMINAL_ORDER_STATUSES:
            if hasattr(self.broker, "clear_unresolved_symbol"):
                self.broker.clear_unresolved_symbol(symbol)
        elif hasattr(self.broker, "unresolved_symbols"):
            self.broker.unresolved_symbols.add(symbol)
            save_metadata = getattr(self.broker, "_save_metadata", None)
            if callable(save_metadata):
                save_metadata()
        return result

    def reconcile_pending(self, symbol: str | None = None) -> list[ReconciliationResult]:
        """Reconcile persisted external orders without evaluating strategy."""
        metadata = getattr(self.broker, "order_metadata", {})
        candidates: dict[str, dict[str, Any]] = {}
        durable_repository = self.repository is not None and hasattr(self.repository, "pending_reconciliations")
        if durable_repository:
            for item in self.repository.pending_reconciliations(symbol):
                client_id = item.get("client_order_id")
                if client_id:
                    candidates[client_id] = item
        terminal_metadata_symbols: set[str] = set()
        for item in metadata.values():
            client_id = item.get("client_order_id")
            if client_id:
                existing = candidates.get(client_id)
                metadata_status = str(item.get("status", "UNKNOWN")).upper()
                if metadata_status in TERMINAL_ORDER_STATUSES:
                    if item.get("symbol"):
                        terminal_metadata_symbols.add(str(item["symbol"]))
                    if existing is None:
                        continue
                # When supervised persistence is available, its pending
                # projection is authoritative.  A JSON-only metadata record
                # must not resurrect work that durable state resolved (or
                # hide a durable failure by replacing its context).
                if existing is None and durable_repository:
                    continue
                if existing is None:
                    candidates[client_id] = dict(item)
                else:
                    candidates[client_id] = {**item, **existing}
        results: list[ReconciliationResult] = []
        for item in candidates.values():
            client_order_id = item.get("client_order_id")
            item_symbol = item.get("symbol")
            if not client_order_id or (symbol and item_symbol != symbol):
                continue
            results.append(self.reconcile_order(cycle_id=item.get("cycle_id") or client_order_id, symbol=item_symbol, client_order_id=client_order_id, expected_quantity=int(item.get("submitted_quantity", 0) or 0), expected_position=item.get("expected_position"), pre_trade_position=item.get("pre_trade_position"), require_position=True))
        if hasattr(self.broker, "clear_unresolved_symbol"):
            pending_symbols = {str(item.get("symbol")) for item in candidates.values() if item.get("symbol")}
            for metadata_symbol in terminal_metadata_symbols - pending_symbols:
                self.broker.clear_unresolved_symbol(metadata_symbol)
        return results

    def _save(self, result: ReconciliationResult) -> None:
        payload: dict[str, Any] = asdict(result)
        payload["last_reconciled_at"] = result.last_reconciled_at.isoformat()
        (self.root / f"reconciliation-{result.cycle_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.repository is not None and hasattr(self.repository, "save_reconciliation"):
            self.repository.save_reconciliation(result)
