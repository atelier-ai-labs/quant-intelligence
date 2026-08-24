from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from quant_intelligence.trading.runtime import (
    PersistentTraderService,
    RuntimeConfigurationError,
    ServiceAlreadyRunning,
    ServiceConfig,
)
from quant_intelligence.trading.status import OperationalStatus


def valid_environment():
    return {
        "QI_TRADING_PERSISTENCE": "postgres",
        "DATABASE_URL": "postgresql+psycopg://example.invalid/qi",
        "APCA_API_KEY_ID": "test-key",
        "APCA_API_SECRET_KEY": "test-secret",
        "APCA_PAPER": "true",
        "APCA_EXECUTION_ENABLED": "true",
        "QI_SYMBOL": "SPY",
    }


class Lease:
    def __init__(self, events):
        self.events = events

    def release(self):
        self.events.append("lock_released")

    def ensure_held(self):
        self.events.append("lock_checked")


class Repository:
    def __init__(self, events, *, lock=True, fail_after=None):
        self.events = events
        self.lock = lock
        self.fail_after = fail_after
        self.checks = 0

    def ensure_connection(self):
        self.checks += 1
        self.events.append("persistence_checked")
        if self.fail_after is not None and self.checks > self.fail_after:
            raise RuntimeError("database unavailable")

    def acquire_service_lock(self, name):
        self.events.append(f"lock:{name}")
        return Lease(self.events) if self.lock else None

    def close(self):
        self.events.append("repository_closed")


class Clock:
    def now(self):
        return datetime(2024, 1, 2, tzinfo=timezone.utc)


class Trader:
    symbol = "SPY"

    def __init__(self, events, *, reconciled=True):
        self.events = events
        self.reconciled = reconciled
        self.status = OperationalStatus(state="stopped")
        self.clock = Clock()

    def start(self):
        self.events.append("trader_started")
        self.status.state = "running"

    def reconcile_now(self):
        self.events.append("reconciled")
        self.status.trading_health = "healthy" if self.reconciled else "halted"
        self.status.halt_reason = None if self.reconciled else "unresolved broker order"
        self.status.unresolved_symbols = () if self.reconciled else ("SPY",)
        return self.reconciled

    def run_due_cycles(self):
        self.events.append("strategy_cycle")
        return []

    def next_decision_time(self):
        return self.clock.now() + timedelta(days=1)

    def stop(self):
        self.events.append("trader_stopped")
        self.status.state = "stopped"

    def _persist_status(self):
        self.events.append("status_persisted")

    def halt(self, reason):
        self.status.trading_health = "halted"
        self.status.halt_reason = reason
        self.status.last_error = reason
        self.events.append("status_persisted")


def test_service_configuration_accepts_safe_paper_runtime():
    config = ServiceConfig.from_env(valid_environment())
    assert config.symbol == "SPY"
    assert config.alpaca.paper is True
    assert config.alpaca.execution_enabled is True
    assert config.window == 200


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"QI_TRADING_PERSISTENCE": "json"}, "postgres"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"APCA_PAPER": "false"}, "live mode"),
        ({"APCA_EXECUTION_ENABLED": "false"}, "EXECUTION_ENABLED"),
        ({"QI_MAX_ORDER_SHARES": "0"}, "MAX_ORDER_SHARES"),
    ],
)
def test_service_configuration_fails_closed(change, message):
    env = valid_environment()
    env.update(change)
    with pytest.raises(RuntimeConfigurationError, match=message):
        ServiceConfig.from_env(env)


def test_startup_reconciles_before_strategy_execution_and_releases_resources():
    events = []
    service = PersistentTraderService(trader=Trader(events), repository=Repository(events), lock_name="qi-test")
    service.run(max_iterations=1)
    assert events.index("persistence_checked") < events.index("lock:qi-test") < events.index("trader_started")
    assert events.index("reconciled") < events.index("strategy_cycle")
    assert events[-3:] == ["trader_stopped", "lock_released", "repository_closed"]


def test_unresolved_startup_reconciliation_blocks_strategy():
    events = []
    service = PersistentTraderService(trader=Trader(events, reconciled=False), repository=Repository(events), lock_name="qi-test")
    service.run(max_iterations=1)
    assert "strategy_cycle" not in events
    assert events.count("reconciled") == 2


def test_second_instance_is_refused_before_trader_start():
    events = []
    service = PersistentTraderService(trader=Trader(events), repository=Repository(events, lock=False), lock_name="qi-test")
    with pytest.raises(ServiceAlreadyRunning):
        service.run(max_iterations=1)
    assert "trader_started" not in events
    assert events[-1] == "repository_closed"


def test_persistence_failure_halts_before_next_strategy_cycle():
    events = []
    trader = Trader(events)
    service = PersistentTraderService(trader=trader, repository=Repository(events, fail_after=1), lock_name="qi-test")
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.run(max_iterations=1)
    assert "strategy_cycle" not in events
    assert trader.status.trading_health == "halted"
    assert trader.status.halt_reason == "required service dependency unavailable"


def test_pre_requested_shutdown_is_graceful_and_never_runs_strategy():
    events = []
    stopped = Event()
    stopped.set()
    service = PersistentTraderService(trader=Trader(events), repository=Repository(events), lock_name="qi-test", stop_event=stopped)
    service.run()
    assert "trader_started" not in events
    assert "reconciled" not in events
    assert "strategy_cycle" not in events
    assert "lock_released" in events
