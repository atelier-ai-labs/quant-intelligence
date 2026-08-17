import logging
from datetime import datetime
from .audit import TradingAuditStore
from .clock import Clock, SystemClock
from .cycle import TradingCycleService
from .models import TradingDecision
from .scheduler import IntervalScheduler
from .status import OperationalStatus, StatusStore

logger = logging.getLogger(__name__)

class AutonomousTrader:
    def __init__(self, *, symbol: str, cycle_service: TradingCycleService, scheduler: IntervalScheduler, clock: Clock | None = None, status_store: StatusStore | None = None):
        self.symbol = symbol; self.cycle_service = cycle_service; self.scheduler = scheduler; self.clock = clock or SystemClock(); self.status_store = status_store; self.status = status_store.load() if status_store else OperationalStatus(); self.status.state = "stopped"; self._persist_status()

    def start(self) -> None:
        self.status.state = "running"; self._persist_status(); logger.info("trader_started", extra={"symbol": self.symbol})

    def stop(self) -> None:
        self.status.state = "stopped"; self._persist_status(); logger.info("trader_stopped", extra={"symbol": self.symbol})

    def run_due_cycles(self, now: datetime | None = None) -> list[TradingDecision]:
        if self.status.state != "running": return []
        current = now or self.clock.now(); decisions = []
        while True:
            scheduled_at = self.scheduler.consume_due(current)
            if scheduled_at is None: break
            logger.info("scheduler_trigger", extra={"symbol": self.symbol, "scheduled_at": scheduled_at.isoformat()})
            logger.info("cycle_started", extra={"symbol": self.symbol})
            try:
                decision = self.cycle_service.run(self.symbol, now=scheduled_at)
            except Exception as exc:
                self.status.last_error = str(exc); self.status.last_cycle_outcome = "FAILED"; self._persist_status(); logger.exception("cycle_failed", extra={"symbol": self.symbol}); self.stop(); break
            decisions.append(decision); self.status.update_from_decision(decision); self._persist_status()
            if decision.outcome == "HOLD": logger.info("hold_no_order", extra={"cycle_id": decision.cycle_id})
            elif decision.outcome == "NO_TRADE": logger.info("risk_or_data_rejection", extra={"cycle_id": decision.cycle_id, "reason": decision.risk_decision.reason})
            elif decision.outcome == "EXECUTED": logger.info("execution", extra={"cycle_id": decision.cycle_id, "order_id": decision.submitted_order.order_id if decision.submitted_order else None})
            logger.info("cycle_completed", extra={"cycle_id": decision.cycle_id, "outcome": decision.outcome})
        return decisions

    def _persist_status(self) -> None:
        if self.status_store: self.status_store.save(self.status)
