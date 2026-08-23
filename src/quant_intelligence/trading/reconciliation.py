"""Small, evidence-preserving reconciliation service for external brokers."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .broker import BrokerUnavailable


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

    def reconcile_order(self, *, cycle_id: str, symbol: str, client_order_id: str, expected_quantity: int, expected_position: int | None = None) -> ReconciliationResult:
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
            mismatch = (expected_position is not None and expected_position != observed_position) or (filled not in {0, expected_quantity})
            result = ReconciliationResult(cycle_id, symbol, client_order_id, "MISMATCH" if mismatch else order.status, expected_quantity, filled, expected_position, observed_position, order.broker_order_id, now, None)
        self._save(result)
        if hasattr(self.broker, "clear_unresolved_symbol") and result.status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "MISSING", "MISMATCH"}:
            self.broker.clear_unresolved_symbol(symbol)
        return result

    def reconcile_pending(self, symbol: str | None = None) -> list[ReconciliationResult]:
        """Reconcile persisted external orders without evaluating strategy."""
        metadata = getattr(self.broker, "order_metadata", {})
        results: list[ReconciliationResult] = []
        for item in metadata.values():
            client_order_id = item.get("client_order_id")
            item_symbol = item.get("symbol")
            if not client_order_id or (symbol and item_symbol != symbol):
                continue
            results.append(self.reconcile_order(cycle_id=item.get("cycle_id") or client_order_id, symbol=item_symbol, client_order_id=client_order_id, expected_quantity=int(item.get("submitted_quantity", 0) or 0)))
        return results

    def _save(self, result: ReconciliationResult) -> None:
        payload: dict[str, Any] = asdict(result)
        payload["last_reconciled_at"] = result.last_reconciled_at.isoformat()
        (self.root / f"reconciliation-{result.cycle_id}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.repository is not None and hasattr(self.repository, "save_reconciliation"):
            self.repository.save_reconciliation(result)
