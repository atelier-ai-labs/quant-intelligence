from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

from quant_intelligence.trading.runtime import (
    CriticalResetError,
    CriticalRiskResetService,
    PersistentTraderService,
    RuntimeConfigurationError,
    ServiceAlreadyRunning,
    ServiceConfig,
)
from quant_intelligence.trading.models import Position
from quant_intelligence.trading.risk import RiskEvaluation, RiskMode, RiskState
from quant_intelligence.trading.status import OperationalStatus
from quant_intelligence.cli import main as cli_main


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

    def record_operational_event(self, event_type, **kwargs):
        self.events.append(f"operational:{event_type}")

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
    assert config.max_position_allocation == 0.20
    assert config.max_gross_exposure == 1.0 and config.max_leverage == 1.0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"QI_TRADING_PERSISTENCE": "json"}, "postgres"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"APCA_PAPER": "false"}, "live mode"),
        ({"APCA_EXECUTION_ENABLED": "false"}, "EXECUTION_ENABLED"),
        ({"QI_MAX_ORDER_SHARES": "0"}, "MAX_ORDER_SHARES"),
        ({"QI_MAX_POSITION_ALLOCATION": "0.2001"}, "at most 0.2"),
        ({"QI_MAX_GROSS_EXPOSURE": "1.01"}, "at most 1.0"),
        ({"QI_MAX_LEVERAGE": "1.01"}, "at most 1.0"),
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
    assert events[-6:] == ["trader_stopped", "operational:TRADER_STOPPED", "lock_released", "operational:SERVICE_LOCK_RELEASED", "operational:SERVICE_STOPPED", "repository_closed"]


def test_unresolved_startup_reconciliation_blocks_strategy():
    events = []
    service = PersistentTraderService(trader=Trader(events, reconciled=False), repository=Repository(events), lock_name="qi-test")
    service.run(max_iterations=1)
    assert "strategy_cycle" not in events
    assert events.count("reconciled") == 2


def test_portfolio_risk_halt_still_reaches_due_session_evaluation():
    events = []
    trader = Trader(events)
    trader.status.risk_mode = "DAILY_HALT"
    trader.status.trading_health = "halted"
    trader.status.broker_health = "healthy"
    trader.status.last_reconciliation_status = "HEALTHY"
    service = PersistentTraderService(trader=trader, repository=Repository(events), lock_name="qi-test")
    service.run(max_iterations=1)
    assert "strategy_cycle" in events


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


def test_critical_reset_is_exclusive_broker_verified_and_order_free():
    events = []

    class ResetRepository(Repository):
        def reset_critical_risk(self, **kwargs):
            events.append(("reset", kwargs))
            return RiskEvaluation(RiskState(session_id=kwargs["session_id"], mode=RiskMode.NORMAL), "reset", "CRITICAL_HALT_RESET")

    class Broker:
        config = type("Config", (), {"paper": True})()
        submitted = 0

        def preflight(self, symbol):
            events.append("preflight")
            return 600.0, (Position(symbol, 10, 10.0),)

        def get_account_equity(self):
            events.append("equity")
            return 700.0

        def submit_order(self, *args, **kwargs):
            self.submitted += 1

    class Reconciliation:
        def reconcile_pending(self, symbol=None):
            events.append("reconciled")
            return []

    class Sessions:
        def latest_completed_session(self, now, delay):
            return type("Session", (), {"session_date": now.date()})()

    broker = Broker()
    repository = ResetRepository(events)
    service = CriticalRiskResetService(repository=repository, broker=broker, reconciliation_service=Reconciliation(), session_provider=Sessions(), symbol="SPY", lock_name="qi-test", clock=Clock())
    result = service.reset(operator="ops@example.com", reason="verified account and incident")
    assert result.state.mode == RiskMode.NORMAL and broker.submitted == 0
    reset_kwargs = next(item[1] for item in events if isinstance(item, tuple) and item[0] == "reset")
    assert reset_kwargs["equity"] == 700 and reset_kwargs["cash"] == 600
    assert reset_kwargs["operator"] == "ops@example.com" and reset_kwargs["reason"] == "verified account and incident"
    assert events.index("lock:qi-test") < events.index("reconciled") < events.index("preflight") < events.index("equity")
    assert "lock_released" in events


def test_critical_reset_refuses_missing_identity_lock_and_unresolved_state():
    class Broker:
        config = type("Config", (), {"paper": True})()

    class Sessions:
        def latest_completed_session(self, now, delay):
            return type("Session", (), {"session_date": now.date()})()

    with pytest.raises(CriticalResetError, match="identity"):
        CriticalRiskResetService(repository=Repository([], lock=True), broker=Broker(), reconciliation_service=object(), session_provider=Sessions(), symbol="SPY", lock_name="qi", clock=Clock()).reset(operator="", reason="reason")
    with pytest.raises(CriticalResetError, match="advisory lock"):
        CriticalRiskResetService(repository=Repository([], lock=False), broker=Broker(), reconciliation_service=object(), session_provider=Sessions(), symbol="SPY", lock_name="qi", clock=Clock()).reset(operator="ops", reason="reason")
    class Unresolved:
        def reconcile_pending(self, symbol=None):
            return [type("Result", (), {"status": "UNKNOWN"})()]
    events = []
    with pytest.raises(CriticalResetError, match="unresolved"):
        CriticalRiskResetService(repository=Repository(events), broker=Broker(), reconciliation_service=Unresolved(), session_provider=Sessions(), symbol="SPY", lock_name="qi", clock=Clock()).reset(operator="ops", reason="reason")
    assert "lock_released" in events


def test_critical_reset_cli_requires_operator_and_has_no_equity_override(monkeypatch):
    monkeypatch.setattr("sys.argv", ["quant-intelligence", "alpaca-risk-reset", "--reason", "verified"])
    with pytest.raises(SystemExit) as missing_operator:
        cli_main()
    assert missing_operator.value.code == 2
    monkeypatch.setattr("sys.argv", ["quant-intelligence", "alpaca-risk-reset", "--operator", "ops", "--reason", "verified", "--equity", "999999"])
    with pytest.raises(SystemExit) as equity_override:
        cli_main()
    assert equity_override.value.code == 2
