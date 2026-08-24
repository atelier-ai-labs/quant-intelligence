import logging
from datetime import datetime, timedelta
from typing import Any

from .broker import BrokerError, BrokerUnavailable
from .clock import Clock, SystemClock
from .cycle import TradingCycleService
from .models import TradingDecision
from .scheduler import IntervalScheduler
from .sessions import MarketSessionProvider
from .status import OperationalStatus, StatusStore

logger = logging.getLogger(__name__)


class AutonomousTrader:
    """Shared autonomous runner for local and Alpaca paper trading."""

    def __init__(self, *, symbol: str, cycle_service: TradingCycleService, scheduler: IntervalScheduler | None = None, clock: Clock | None = None, status_store: StatusStore | None = None, session_provider: MarketSessionProvider | None = None, post_close_delay: timedelta = timedelta(minutes=5), managed_symbols: tuple[str, ...] | None = None, reconciliation_service: Any | None = None):
        self.symbol = symbol
        self.cycle_service = cycle_service
        self.scheduler = scheduler
        self.clock = clock or SystemClock()
        self.status_store = status_store
        self.session_provider = session_provider
        self.post_close_delay = post_close_delay
        self.reconciliation_service = reconciliation_service
        self.status = status_store.load() if status_store else OperationalStatus()
        self._sync_durable_risk_status()
        self.status.state = "stopped"
        self.status.managed_symbols = managed_symbols or (symbol,)
        self._persist_status()

    def start(self) -> None:
        self.status.state = "running"
        self.status.trading_health = "unknown"
        self._persist_status()
        logger.info("trader_started", extra={"symbol": self.symbol})

    def stop(self) -> None:
        self.status.state = "stopped"
        self._persist_status()
        logger.info("trader_stopped", extra={"symbol": self.symbol})

    def next_decision_time(self, now: datetime | None = None) -> datetime | None:
        current = now or self.clock.now()
        if self.session_provider:
            return self.session_provider.next_decision_time(current, self.post_close_delay)
        return self.scheduler.next_run_at if self.scheduler else None

    def run_due_cycles(self, now: datetime | None = None) -> list[TradingDecision]:
        if self.status.state != "running":
            return []
        current = now or self.clock.now()
        return self._run_session_cycle(current) if self.session_provider else self._run_interval_cycles(current)

    def reconcile_now(self) -> bool:
        """Run broker reconciliation without evaluating the strategy."""
        return self._preflight()

    def halt(self, reason: str) -> None:
        """Public fail-closed lifecycle boundary for process supervisors."""
        self._halt(reason)

    def _run_session_cycle(self, current: datetime) -> list[TradingDecision]:
        try:
            session = self.session_provider.latest_completed_session(current, self.post_close_delay)  # type: ignore[union-attr]
        except Exception as exc:
            self._halt(f"market calendar unavailable: {exc}", broker=False)
            return []
        if session is None:
            self.status.next_scheduled_decision = self.next_decision_time(current)
            self._persist_status()
            return []
        session_key = session.session_date.isoformat()
        if not self._preflight():
            return []
        if self.status.latest_completed_session == session_key:
            return []
        logger.info("scheduler_trigger", extra={"symbol": self.symbol, "session": session_key})
        logger.info("cycle_started", extra={"symbol": self.symbol, "session": session_key})
        try:
            decision = self.cycle_service.run(self.symbol, now=current, reconciled=True, session_id=session_key)
        except Exception as exc:
            self.status.last_error = str(exc)
            self.status.last_cycle_outcome = "FAILED"
            self._persist_status()
            logger.exception("cycle_failed", extra={"symbol": self.symbol})
            self.stop()
            return []
        if decision.risk_decision.reason != "stale market data":
            self.status.latest_completed_session = session_key
        self.status.next_scheduled_decision = self.next_decision_time(current)
        self.status.update_from_decision(decision)
        self.status.risk_mode = decision.risk_decision.mode
        self.status.trading_health = "healthy" if not decision.error and decision.risk_decision.mode == "NORMAL" else "halted"
        self.status.halt_reason = decision.error or (None if decision.risk_decision.mode == "NORMAL" else decision.risk_decision.reason)
        self._persist_status()
        self._log_decision(decision)
        return [decision]

    def _run_interval_cycles(self, current: datetime) -> list[TradingDecision]:
        if self.scheduler is None:
            return []
        decisions: list[TradingDecision] = []
        while True:
            scheduled_at = self.scheduler.consume_due(current)
            if scheduled_at is None:
                break
            logger.info("scheduler_trigger", extra={"symbol": self.symbol, "scheduled_at": scheduled_at.isoformat()})
            logger.info("cycle_started", extra={"symbol": self.symbol})
            try:
                decision = self.cycle_service.run(self.symbol, now=scheduled_at)
            except Exception as exc:
                self.status.last_error = str(exc)
                self.status.last_cycle_outcome = "FAILED"
                self._persist_status()
                logger.exception("cycle_failed", extra={"symbol": self.symbol})
                self.stop()
                break
            decisions.append(decision)
            self.status.risk_mode = decision.risk_decision.mode
            if decision.risk_decision.mode != "NORMAL":
                self.status.trading_health = "halted"
                self.status.halt_reason = decision.risk_decision.reason
            self.status.update_from_decision(decision)
            self._persist_status()
            self._log_decision(decision)
        return decisions

    def _preflight(self) -> bool:
        broker: Any = self.cycle_service.broker
        if not self._sync_durable_risk_status():
            return False
        preflight = getattr(broker, "preflight", None)
        if preflight is None:
            self.status.broker_health = "healthy"
            self.status.last_reconciliation_status = "NOT_REQUIRED"
            if self.status.risk_mode == "NORMAL":
                self.status.trading_health = "healthy"
                self.status.halt_reason = None
            else:
                self.status.trading_health = "halted"
                self.status.halt_reason = f"risk mode {self.status.risk_mode}"
            self._persist_status()
            return True
        if self.reconciliation_service is not None:
            results = self.reconciliation_service.reconcile_pending(self.symbol)
            if any(result.status in {"UNKNOWN", "UNRESOLVED", "MISMATCH", "MISSING"} for result in results):
                self._halt("broker reconciliation requires attention")
                return False
        try:
            cash, positions = preflight(self.symbol)
        except (BrokerError, BrokerUnavailable, RuntimeError) as exc:
            self.status.broker_connected = False
            self.status.broker_health = "unavailable"
            self.status.last_reconciliation_status = "BLOCKED"
            self.status.unresolved_symbols = (self.symbol,)
            self.status.unresolved_order_count = 1
            self._halt(f"preflight blocked trading: {exc}")
            logger.warning("reconciliation_blocked", extra={"symbol": self.symbol, "reason": str(exc)})
            return False
        self.status.broker_connected = True
        self.status.broker_health = "healthy"
        self.status.last_reconciliation_status = "HEALTHY"
        self.status.last_reconciliation_timestamp = self.clock.now()
        self.status.current_cash = cash
        self.status.current_positions = tuple(positions)
        self.status.unresolved_symbols = tuple()
        self.status.unresolved_order_count = 0
        if self.status.risk_mode == "NORMAL":
            self.status.trading_health = "healthy"
            self.status.halt_reason = None
        else:
            self.status.trading_health = "halted"
            self.status.halt_reason = f"risk mode {self.status.risk_mode}"
        self._persist_status()
        logger.info("reconciliation_completed", extra={"symbol": self.symbol})
        return True

    def _sync_durable_risk_status(self) -> bool:
        risk_store = getattr(self.cycle_service, "risk_state_store", None)
        if risk_store is None:
            return True
        try:
            state = risk_store.load_risk_state(getattr(self.cycle_service, "risk_account_key", "default"))
        except Exception as exc:
            self.status.risk_mode = "CRITICAL_HALT"
            self.status.trading_health = "halted"
            self.status.halt_reason = f"risk state unavailable: {exc}"
            self.status.last_error = self.status.halt_reason
            self._persist_status()
            return False
        if state is not None:
            self.status.risk_mode = state.mode.value
            if state.mode.value != "NORMAL":
                self.status.trading_health = "halted"
                self.status.halt_reason = f"risk mode {state.mode.value}"
        return True

    def _halt(self, reason: str, *, broker: bool = True) -> None:
        self.status.trading_health = "halted"
        self.status.halt_reason = reason
        self.status.last_error = reason
        if broker:
            self.status.broker_health = "unavailable"
        self._persist_status()

    @staticmethod
    def _log_decision(decision: TradingDecision) -> None:
        if decision.outcome in {"HOLD", "WOULD_HOLD"}:
            logger.info("hold_no_order", extra={"cycle_id": decision.cycle_id})
        elif decision.outcome == "NO_TRADE":
            logger.info("risk_or_data_rejection", extra={"cycle_id": decision.cycle_id, "reason": decision.risk_decision.reason})
        elif decision.submitted_order:
            logger.info("execution", extra={"cycle_id": decision.cycle_id, "order_id": decision.submitted_order.order_id})
        logger.info("cycle_completed", extra={"cycle_id": decision.cycle_id, "outcome": decision.outcome})

    def _persist_status(self) -> None:
        if self.status_store:
            self.status_store.save(self.status)
