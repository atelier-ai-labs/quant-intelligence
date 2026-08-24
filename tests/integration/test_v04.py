from datetime import date, datetime, time, timedelta, timezone

from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading import AlpacaMarketSessionProvider, AutonomousTrader, FakeClock, FixtureMarketSessionProvider, MarketSession, PaperBroker, TradingCycleService
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.risk import RiskConfig, RiskGate
from quant_intelligence.trading.models import Position
from quant_intelligence.trading.status import StatusStore
from quant_intelligence.trading.persistence import SqlAlchemyOperationalRepository
from quant_intelligence.trading.status import OperationalStatus
from tests.integration.test_trading import bars

UTC = timezone.utc


def session(day: date, close_hour: int = 16) -> MarketSession:
    return MarketSession(
        day,
        datetime.combine(day, time(9, 30), tzinfo=timezone(timedelta(hours=-5))).astimezone(UTC),
        datetime.combine(day, time(close_hour), tzinfo=timezone(timedelta(hours=-5))).astimezone(UTC),
        close_hour != 16,
    )


def test_calendar_handles_weekend_holiday_early_close_and_delay():
    provider = FixtureMarketSessionProvider((session(date(2024, 7, 3), 13), session(date(2024, 7, 5))))
    before_close = datetime(2024, 7, 3, 17, 55, tzinfo=UTC)
    after_close = datetime(2024, 7, 3, 18, 5, tzinfo=UTC)
    assert provider.latest_completed_session(before_close, timedelta(minutes=5)) is None
    assert provider.latest_completed_session(after_close, timedelta(minutes=5)).session_date == date(2024, 7, 3)
    assert provider.is_trading_day(date(2024, 7, 4)) is False
    assert provider.next_decision_time(after_close, timedelta(minutes=5)) == session(date(2024, 7, 5)).closes_at + timedelta(minutes=5)


def test_alpaca_calendar_uses_supported_request_boundary():
    class CalendarClient:
        def get_calendar(self, request):
            assert request.start == date(2024, 7, 3) - timedelta(days=14)
            return (type("CalendarItem", (), {"date": date(2024, 7, 3), "open": "09:30", "close": "13:00"})(),)

    provider = AlpacaMarketSessionProvider(CalendarClient())
    result = provider.latest_completed_session(datetime(2024, 7, 3, 18, 5, tzinfo=UTC), timedelta(minutes=5))
    assert result is not None and result.early_close is True


def test_session_runner_executes_once_and_restart_is_idempotent(tmp_path):
    class PreflightPaperBroker(PaperBroker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.preflight_calls = 0

        def preflight(self, symbol):
            self.preflight_calls += 1
            return self.cash, self.get_positions()

    current = datetime(2024, 7, 3, 19, tzinfo=UTC)
    clock = FakeClock(current)
    provider = FixtureMarketDataProvider("SYNTH", bars([10, 11, 12]), data_timestamp=datetime(2024, 7, 3, tzinfo=UTC), max_age=timedelta(days=2))
    broker = PreflightPaperBroker(1000, transaction_cost_bps=0, state_path=tmp_path / "broker.json")
    service = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=RiskGate(RiskConfig(max_order_notional=1000), transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"), max_execution_shares=10)
    calendar = FixtureMarketSessionProvider((session(date(2024, 7, 3), 13),))
    first = AutonomousTrader(symbol="SYNTH", cycle_service=service, clock=clock, status_store=StatusStore(tmp_path / "status.json"), session_provider=calendar, post_close_delay=timedelta(minutes=5))
    first.start()
    decisions = first.run_due_cycles()
    assert len(decisions) == 1 and decisions[0].signal.value == "BUY"
    assert len(broker.orders) == 1
    restarted = AutonomousTrader(symbol="SYNTH", cycle_service=service, clock=clock, status_store=StatusStore(tmp_path / "status.json"), session_provider=calendar, post_close_delay=timedelta(minutes=5))
    restarted.start()
    assert restarted.run_due_cycles() == []
    assert len(broker.orders) == 1
    assert broker.preflight_calls == 2
    assert restarted.status.trading_health == "healthy"


def test_unmanaged_position_is_preserved(tmp_path):
    class BrokerWithUnmanagedPosition(PaperBroker):
        def is_position_managed(self, symbol: str) -> bool:
            return False

    broker = BrokerWithUnmanagedPosition(1000, transaction_cost_bps=0)
    broker.positions["SYNTH"] = Position("SYNTH", 10, 10)
    provider = FixtureMarketDataProvider("SYNTH", bars([12, 11, 10]), data_timestamp=datetime(2024, 7, 3, tzinfo=UTC), max_age=timedelta(days=2))
    decision = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"), max_execution_shares=10).run("SYNTH", datetime(2024, 7, 3, 19, tzinfo=UTC))
    assert decision.signal.value == "HOLD"
    assert decision.signal_reason == "unmanaged broker position preserved"


def test_daily_halt_reconciles_and_resets_on_new_fresh_completed_session(tmp_path):
    class PreflightPaperBroker(PaperBroker):
        def preflight(self, symbol):
            return self.cash, self.get_positions()

    old = datetime(2024, 7, 2, 19, tzinfo=UTC)
    current = datetime(2024, 7, 3, 19, tzinfo=UTC)
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'risk.db'}", create_schema=True)
    repository.evaluate_risk_state(equity=1000, session_id="2024-07-02", timestamp=old, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=949, session_id="2024-07-02", timestamp=old, reconciled=True, valid_session=True)
    repository.save_status(OperationalStatus(state="stopped", trading_health="halted", broker_health="healthy", last_reconciliation_status="HEALTHY", risk_mode="DAILY_HALT"))
    broker = PreflightPaperBroker(1000, transaction_cost_bps=0)
    provider = FixtureMarketDataProvider("SYNTH", bars([10, 11, 12]), data_timestamp=datetime(2024, 7, 3, tzinfo=UTC), max_age=timedelta(days=2))
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=0, risk_mode="DAILY_HALT"), audit_store=repository, max_execution_shares=10, risk_state_store=repository)
    trader = AutonomousTrader(symbol="SYNTH", cycle_service=cycle, clock=FakeClock(current), status_store=repository, session_provider=FixtureMarketSessionProvider((session(date(2024, 7, 3), 13),)), post_close_delay=timedelta(minutes=5))
    trader.start()
    decisions = trader.run_due_cycles()
    assert len(decisions) == 1 and decisions[0].risk_decision.mode == "NORMAL"
    assert repository.load_risk_state().session_id == "2024-07-03"
    assert repository.load_risk_state().daily_baseline_equity == 1000
    assert trader.status.risk_mode == "NORMAL" and trader.status.trading_health == "healthy"


def test_daily_halt_session_runner_executes_full_risk_reduction(tmp_path):
    class PreflightPaperBroker(PaperBroker):
        def preflight(self, symbol):
            return self.cash, self.get_positions()

    current = datetime(2024, 7, 3, 19, tzinfo=UTC)
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'reduction.db'}", create_schema=True)
    repository.evaluate_risk_state(equity=1000, session_id="2024-07-03", timestamp=current, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=949, session_id="2024-07-03", timestamp=current, reconciled=True, valid_session=True)
    repository.save_status(OperationalStatus(state="stopped", trading_health="halted", broker_health="healthy", last_reconciliation_status="HEALTHY", risk_mode="DAILY_HALT"))
    broker = PreflightPaperBroker(900, transaction_cost_bps=0)
    broker.positions["SYNTH"] = Position("SYNTH", 10, 10)
    provider = FixtureMarketDataProvider("SYNTH", bars([12, 11, 10]), data_timestamp=datetime(2024, 7, 3, tzinfo=UTC), max_age=timedelta(days=2))
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=0, risk_mode="DAILY_HALT"), audit_store=repository, risk_state_store=repository)
    trader = AutonomousTrader(symbol="SYNTH", cycle_service=cycle, clock=FakeClock(current), status_store=repository, session_provider=FixtureMarketSessionProvider((session(date(2024, 7, 3), 13),)), post_close_delay=timedelta(minutes=5))
    trader.start()
    decision = trader.run_due_cycles()[0]
    assert decision.signal.value == "SELL" and decision.outcome == "EXECUTED"
    assert decision.risk_decision.mode == "DAILY_HALT" and broker.get_positions() == ()
    assert trader.status.risk_mode == "DAILY_HALT" and trader.status.trading_health == "halted"
