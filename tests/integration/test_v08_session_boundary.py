from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from quant_intelligence.models import Bar
from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.alpaca import AlpacaConfig, AlpacaMarketDataProvider
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.autonomous import AutonomousTrader
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.clock import FakeClock
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import RiskDecision, SignalAction, TradingDecision
from quant_intelligence.trading.persistence import SqlAlchemyOperationalRepository, TradingCycleRow
from quant_intelligence.trading.risk import RiskGate
from quant_intelligence.trading.sessions import FixtureMarketSessionProvider, MarketSession
from quant_intelligence.trading.status import StatusStore
from sqlalchemy import select
from sqlalchemy.orm import Session


UTC = timezone.utc
NOW = datetime(2026, 8, 24, 23, 34, tzinfo=UTC)
SESSION = MarketSession(date(2026, 8, 24), datetime(2026, 8, 24, 13, 30, tzinfo=UTC), datetime(2026, 8, 24, 20, tzinfo=UTC))


def alpaca_bar(day: date, close: float) -> SimpleNamespace:
    return SimpleNamespace(timestamp=datetime.combine(day, time(20), tzinfo=UTC), open=close, high=close, low=close, close=close, volume=1000)


class BoundaryClient:
    def __init__(self, bars):
        self.bars = bars
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return {"data": {"SPY": self.bars}}


def alpaca_provider(bars):
    client = BoundaryClient(bars)
    provider = AlpacaMarketDataProvider("SPY", 1, AlpacaConfig("key", "secret"), data_client=client, request_builder=lambda symbol, start, end, feed: {"start": start, "end": end})
    return provider, client


def test_provider_excludes_current_bar_until_session_safety_delay_then_includes_it():
    provider, client = alpaca_provider([alpaca_bar(date(2026, 8, 21), 101), alpaca_bar(date(2026, 8, 24), 104)])
    before_delay = datetime(2026, 8, 24, 20, 4, 59, tzinfo=UTC)
    try:
        provider.get_completed_bars("SPY", before_delay, completed_session=SESSION, post_close_delay=timedelta(minutes=5))
    except Exception as exc:
        assert "safety delay" in str(exc)
    else:
        raise AssertionError("current session must remain unavailable before the safety delay")
    snapshot = provider.get_completed_bars("SPY", NOW, completed_session=SESSION, post_close_delay=timedelta(minutes=5))
    assert snapshot.data_timestamp.date() == SESSION.session_date
    assert snapshot.latest_price == 104
    assert client.requests[-1]["end"] == datetime(2026, 8, 25, tzinfo=UTC)


def test_calendar_boundary_preserves_weekend_holiday_and_early_close_semantics():
    early = MarketSession(date(2026, 8, 21), datetime(2026, 8, 21, 13, 30, tzinfo=UTC), datetime(2026, 8, 21, 17, tzinfo=UTC), early_close=True)
    provider = FixtureMarketSessionProvider((early, SESSION))
    assert provider.latest_completed_session(datetime(2026, 8, 21, 17, 4, tzinfo=UTC), timedelta(minutes=5)) is None
    assert provider.latest_completed_session(datetime(2026, 8, 21, 17, 5, tzinfo=UTC), timedelta(minutes=5)).session_date == date(2026, 8, 21)
    assert provider.latest_completed_session(datetime(2026, 8, 22, 12, tzinfo=UTC), timedelta(minutes=5)).session_date == date(2026, 8, 21)
    assert provider.is_trading_day(date(2026, 8, 22)) is False
    assert provider.is_trading_day(date(2026, 8, 20)) is False


def test_autonomous_cycle_passes_authoritative_session_to_data_and_agrees(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (20, 21, 24)]
    market = FixtureMarketDataProvider("SPY", bars, max_age=timedelta(days=2))
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=market, risk_gate=RiskGate(), audit_store=TradingAuditStore(tmp_path / "audit"), execution_mode="observation")
    trader = AutonomousTrader(symbol="SPY", cycle_service=cycle, clock=FakeClock(NOW), status_store=StatusStore(tmp_path / "status.json"), session_provider=FixtureMarketSessionProvider((SESSION,)), post_close_delay=timedelta(minutes=5))
    trader.start()
    decisions = trader.run_due_cycles()
    assert len(decisions) == 1
    assert decisions[0].market_session == "2026-08-24"
    assert decisions[0].data_timestamp.date() == SESSION.session_date


def test_session_identity_precedes_existing_lookup_for_stale_data(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (19, 20, 21)]
    provider = FixtureMarketDataProvider("SPY", bars, max_age=timedelta(days=10))
    service = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=provider, risk_gate=RiskGate(), audit_store=TradingAuditStore(tmp_path / "audit"), execution_mode="observation")
    old = service.run("SPY", NOW, session_id="2026-08-21")
    stale = service.run("SPY", NOW, session_id="2026-08-24")
    assert old.market_session == "2026-08-21"
    assert stale.market_session == "2026-08-24"
    assert stale.cycle_id != old.cycle_id
    assert stale.error == "invalid completed market session"


def test_completed_session_boundary_is_fail_closed_even_without_explicit_session_id(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (19, 20, 21)]
    service = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=FixtureMarketDataProvider("SPY", bars, max_age=timedelta(days=10)), risk_gate=RiskGate(), audit_store=TradingAuditStore(tmp_path / "audit"), execution_mode="observation")
    decision = service.run("SPY", NOW, completed_session=SESSION, post_close_delay=timedelta(minutes=5))
    assert decision.error == "invalid completed market session"


def test_autonomous_retries_target_session_after_invalid_data_becomes_available(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (19, 20, 21)]
    market = FixtureMarketDataProvider("SPY", bars, data_timestamp=datetime(2026, 8, 21, tzinfo=UTC), max_age=timedelta(days=10))
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=market, risk_gate=RiskGate(), audit_store=TradingAuditStore(tmp_path / "audit"), execution_mode="observation")
    trader = AutonomousTrader(symbol="SPY", cycle_service=cycle, clock=FakeClock(NOW), status_store=StatusStore(tmp_path / "status.json"), session_provider=FixtureMarketSessionProvider((SESSION,)), post_close_delay=timedelta(minutes=5))
    trader.start()
    first = trader.run_due_cycles()[0]
    assert first.error == "invalid completed market session"
    assert trader.status.latest_completed_session is None
    market.bars = tuple(bars + [Bar(date(2026, 8, 24), 124, 124, 124, 124, 1000)])
    market.data_timestamp = datetime(2026, 8, 24, tzinfo=UTC)
    second = trader.run_due_cycles()[0]
    assert second.data_timestamp.date() == SESSION.session_date
    assert trader.status.latest_completed_session == "2026-08-24"


def test_same_session_is_idempotent_and_persistence_keeps_authoritative_session(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (20, 21, 24)]
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'cycles.db'}", create_schema=True)
    service = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=FixtureMarketDataProvider("SPY", bars, max_age=timedelta(days=2)), risk_gate=RiskGate(), audit_store=repository, execution_mode="observation")
    first = service.run("SPY", NOW, session_id="2026-08-24")
    second = service.run("SPY", NOW, session_id="2026-08-24")
    assert second.cycle_id == first.cycle_id
    assert second.outcome == first.outcome
    assert second.market_session == first.market_session
    with Session(repository.engine) as session:
        row = session.scalar(select(TradingCycleRow).where(TradingCycleRow.id == first.cycle_id))
    assert row.market_session == "2026-08-24"
    assert row.market_data_timestamp.date() == date(2026, 8, 24)
    assert row.mode == "observation"


def test_legacy_cycle_id_is_reused_for_same_session_after_identity_upgrade(tmp_path):
    bars = [Bar(date(2026, 8, day), 100 + day, 100 + day, 100 + day, 100 + day, 1000) for day in (19, 20, 21)]
    audit = TradingAuditStore(tmp_path / "audit")
    service = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000), market_data=FixtureMarketDataProvider("SPY", bars, max_age=timedelta(days=10)), risk_gate=RiskGate(), audit_store=audit, execution_mode="paper_execution")
    legacy_id = service._legacy_cycle_id("SPY", datetime(2026, 8, 21, tzinfo=UTC))
    legacy = TradingDecision(legacy_id, NOW, "SPY", "sma_trend", {"window": 3}, datetime(2026, 8, 21, tzinfo=UTC), SignalAction.HOLD, "legacy", None, None, RiskDecision(True, "no order required", None), None, None, None, "HOLD")
    audit.save(legacy)
    repeated = service.run("SPY", NOW, session_id="2026-08-21")
    assert repeated.cycle_id == legacy_id
