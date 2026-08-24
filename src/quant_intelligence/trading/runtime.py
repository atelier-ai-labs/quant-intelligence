"""Production lifecycle for the existing autonomous Alpaca paper trader."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

from quant_intelligence.strategies import SmaTrendStrategy

from .alpaca import AlpacaBroker, AlpacaConfig, AlpacaConfigurationError, AlpacaMarketDataProvider
from .autonomous import AutonomousTrader
from .clock import SystemClock
from .cycle import TradingCycleService
from .persistence import PersistenceConfigurationError, SqlAlchemyOperationalRepository, operational_repository
from .reconciliation import BrokerReconciliation
from .risk import RiskConfig, RiskGate
from .sessions import AlpacaMarketSessionProvider

logger = logging.getLogger(__name__)


class RuntimeConfigurationError(RuntimeError):
    pass


class ServiceAlreadyRunning(RuntimeError):
    pass


def _integer(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeConfigurationError(f"{name} must be at least {minimum}")
    return value


def _number(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be numeric") from exc
    if value <= minimum or (maximum is not None and value > maximum):
        limit = f" and at most {maximum}" if maximum is not None else ""
        raise RuntimeConfigurationError(f"{name} must be greater than {minimum}{limit}")
    return value


@dataclass(frozen=True)
class ServiceConfig:
    database_url: str
    alpaca: AlpacaConfig
    symbol: str = "SPY"
    window: int = 200
    audit_dir: Path = Path("alpaca_audit")
    post_close_delay_minutes: float = 5.0
    max_position_allocation: float = 0.25
    max_order_notional: float = 2500.0
    max_order_shares: int = 10
    reconciliation_interval_seconds: float = 60.0
    service_lock_name: str = "quant-intelligence:alpaca-paper"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ServiceConfig":
        env = environ if environ is not None else os.environ
        if env.get("QI_TRADING_PERSISTENCE", "").lower() != "postgres":
            raise RuntimeConfigurationError("QI_TRADING_PERSISTENCE=postgres is required for the persistent service")
        database_url = env.get("DATABASE_URL", "")
        if not database_url:
            raise RuntimeConfigurationError("DATABASE_URL is required for the persistent service")
        try:
            alpaca = AlpacaConfig.from_env(dict(env))
        except AlpacaConfigurationError as exc:
            raise RuntimeConfigurationError(str(exc)) from exc
        if not alpaca.execution_enabled:
            raise RuntimeConfigurationError("APCA_EXECUTION_ENABLED=true is required for the persistent paper service")
        symbol = env.get("QI_SYMBOL", "SPY").strip().upper()
        if not symbol or not symbol.replace(".", "").replace("-", "").isalnum():
            raise RuntimeConfigurationError("QI_SYMBOL is invalid")
        lock_name = env.get("QI_SERVICE_LOCK_NAME", "quant-intelligence:alpaca-paper").strip()
        if not lock_name:
            raise RuntimeConfigurationError("QI_SERVICE_LOCK_NAME must not be empty")
        return cls(
            database_url=database_url,
            alpaca=alpaca,
            symbol=symbol,
            window=_integer(env, "QI_SMA_WINDOW", 200, minimum=2),
            audit_dir=Path(env.get("QI_AUDIT_DIR", "alpaca_audit")),
            post_close_delay_minutes=_number(env, "QI_POST_CLOSE_DELAY_MINUTES", 5.0),
            max_position_allocation=_number(env, "QI_MAX_POSITION_ALLOCATION", 0.25, maximum=1.0),
            max_order_notional=_number(env, "QI_MAX_ORDER_NOTIONAL", 2500.0),
            max_order_shares=_integer(env, "QI_MAX_ORDER_SHARES", 10),
            reconciliation_interval_seconds=_number(env, "QI_RECONCILIATION_INTERVAL_SECONDS", 60.0),
            service_lock_name=lock_name,
        )


class PersistentTraderService:
    """Supervises one existing AutonomousTrader without owning trading logic."""

    def __init__(self, *, repository: Any, lock_name: str, trader: AutonomousTrader | None = None, trader_factory: Callable[[], AutonomousTrader] | None = None, symbol: str | None = None, reconciliation_interval_seconds: float = 60.0, stop_event: Event | None = None):
        if (trader is None) == (trader_factory is None):
            raise ValueError("provide exactly one of trader or trader_factory")
        self.trader = trader
        self._trader_factory = trader_factory
        self.symbol = symbol or (trader.symbol if trader is not None else "unknown")
        self.repository = repository
        self.lock_name = lock_name
        self.reconciliation_interval_seconds = reconciliation_interval_seconds
        self.stop_event = stop_event or Event()
        self._lock: Any | None = None

    def request_stop(self) -> None:
        logger.info("service_stop_requested")
        self.stop_event.set()

    def run(self, *, max_iterations: int | None = None) -> None:
        logger.info("service_starting", extra={"symbol": self.symbol})
        started = False
        try:
            self.repository.ensure_connection()
            logger.info("persistence_ready")
            self._lock = self.repository.acquire_service_lock(self.lock_name)
            if self._lock is None:
                raise ServiceAlreadyRunning("another Quant Intelligence service instance owns the runtime lock")
            logger.info("service_lock_acquired")
            if self.stop_event.is_set():
                return
            if self.trader is None:
                self.trader = self._trader_factory()  # type: ignore[misc]
            self.trader.start()
            started = True
            if not self.trader.reconcile_now():
                logger.error("trading_halted", extra={"reason": self.trader.status.halt_reason})
            else:
                logger.info("startup_reconciliation_ready")
            iterations = 0
            while not self.stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
                self._lock.ensure_held()
                self.repository.ensure_connection()
                if self.trader.status.trading_health != "healthy" or self.trader.status.unresolved_symbols:
                    self.trader.reconcile_now()
                else:
                    self.trader.run_due_cycles()
                iterations += 1
                if self.stop_event.is_set() or (max_iterations is not None and iterations >= max_iterations):
                    break
                self.stop_event.wait(self._next_wait_seconds())
        except (PersistenceConfigurationError, ServiceAlreadyRunning):
            logger.exception("service_startup_failed")
            raise
        except Exception as exc:
            if started and self.trader is not None:
                try:
                    self.trader.halt("required service dependency unavailable")
                except Exception:
                    logger.error("status_persistence_failed")
            logger.exception("service_fatal_failure", extra={"error_type": type(exc).__name__})
            raise
        finally:
            if started and self.trader is not None:
                try:
                    self.trader.stop()
                except Exception:
                    logger.exception("service_shutdown_status_failed")
            if self._lock is not None:
                self._lock.release()
                logger.info("service_lock_released")
            close = getattr(self.repository, "close", None)
            if close is not None:
                close()
            logger.info("service_stopped")

    def _next_wait_seconds(self) -> float:
        if self.trader is None:
            return self.reconciliation_interval_seconds
        if self.trader.status.unresolved_symbols or self.trader.status.trading_health != "healthy":
            return self.reconciliation_interval_seconds
        next_time = self.trader.next_decision_time()
        if next_time is None:
            return self.reconciliation_interval_seconds
        return max(1.0, (next_time - self.trader.clock.now()).total_seconds())


def build_persistent_service(config: ServiceConfig, *, stop_event: Event | None = None) -> PersistentTraderService:
    repository = operational_repository(config.audit_dir, database_url=config.database_url, mode="postgres")
    if not isinstance(repository, SqlAlchemyOperationalRepository):
        raise RuntimeConfigurationError("persistent service requires PostgreSQL operational persistence")
    def make_trader() -> AutonomousTrader:
        provider = AlpacaMarketDataProvider(config.symbol, config.window, config.alpaca)
        broker = AlpacaBroker(config.alpaca, state_path=config.audit_dir / "alpaca_orders.json")
        reconciliation = BrokerReconciliation(broker, config.audit_dir / "reconciliation", repository=repository)
        cycle = TradingCycleService(
            strategy=SmaTrendStrategy(config.window),
            broker=broker,
            market_data=provider,
            risk_gate=RiskGate(RiskConfig(max_position_allocation=config.max_position_allocation, max_order_notional=config.max_order_notional)),
            audit_store=repository,
            execution_mode="paper_execution",
            max_execution_shares=config.max_order_shares,
        )
        trader = AutonomousTrader(
            symbol=config.symbol,
            cycle_service=cycle,
            clock=SystemClock(),
            status_store=repository,
            session_provider=AlpacaMarketSessionProvider(broker.client),
            post_close_delay=timedelta(minutes=config.post_close_delay_minutes),
            reconciliation_service=reconciliation,
        )
        trader.status.mode = "paper"
        trader.status.execution_enabled = True
        return trader

    return PersistentTraderService(trader_factory=make_trader, symbol=config.symbol, repository=repository, lock_name=config.service_lock_name, reconciliation_interval_seconds=config.reconciliation_interval_seconds, stop_event=stop_event)
