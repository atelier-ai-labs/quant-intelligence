from datetime import datetime, timedelta, timezone

import pytest

from quant_intelligence.models import Bar
from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import OrderIntent, PortfolioSnapshot, Position, SignalAction
from quant_intelligence.trading.risk import RiskConfig, RiskGate, RiskMode, RiskStateService
from quant_intelligence.trading.persistence import RiskEventRow, SqlAlchemyOperationalRepository
from quant_intelligence.trading.autonomous import AutonomousTrader
from quant_intelligence.trading.clock import FakeClock
from quant_intelligence.trading.sessions import FixtureMarketSessionProvider
from sqlalchemy import select
from sqlalchemy.orm import Session


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def snapshot(*, equity=1000, cash=1000, positions=(), asset_value=0, valuation_complete=True, positions_complete=True):
    return PortfolioSnapshot(NOW, cash, tuple(positions), asset_value, equity, 0, valuation_complete=valuation_complete, positions_complete=positions_complete)


def test_resulting_position_and_gross_are_equity_scaled_and_boundary_safe():
    assert RiskConfig().max_position_allocation == 0.20
    assert RiskConfig().max_gross_exposure == 1.0
    assert RiskConfig().max_leverage == 1.0
    with pytest.raises(ValueError, match="constitutional"):
        RiskConfig(max_position_allocation=0.2001)
    with pytest.raises(ValueError, match="constitutional"):
        RiskConfig(max_gross_exposure=1.01)
    with pytest.raises(ValueError, match="constitutional"):
        RiskConfig(max_leverage=1.01)
    gate = RiskGate(RiskConfig(max_order_notional=10000), transaction_cost_bps=0)
    assert gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 20), snapshot(), 10).approved
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 21), snapshot(), 10).approved
    existing = snapshot(positions=(Position("SPY", 10, 10),), cash=900, asset_value=100)
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 11), existing, 10).approved
    multiple = snapshot(positions=(Position("QQQ", 80, 10),), cash=200, asset_value=800)
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 21), multiple, 10).approved
    margin_like = snapshot(equity=1000, cash=5, asset_value=995)
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 1), margin_like, 10).approved


def test_halted_accounts_only_allow_reductions_and_missing_valuation_fails_closed():
    gate = RiskGate(risk_mode=RiskMode.DAILY_HALT, transaction_cost_bps=0)
    held = snapshot(positions=(Position("SPY", 10, 10),), cash=900, asset_value=100)
    assert gate.evaluate(OrderIntent("SPY", SignalAction.SELL, 10), held, 10).approved
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.BUY, 1), held, 10).approved
    incomplete = snapshot(equity=900, cash=700, positions=(Position("SPY", 10, 10), Position("QQQ", 10, 10)), asset_value=100, valuation_complete=False)
    assert gate.evaluate(OrderIntent("SPY", SignalAction.SELL, 5), incomplete, 10).approved
    assert not RiskGate(transaction_cost_bps=0).evaluate(OrderIntent("SPY", SignalAction.BUY, 1), incomplete, 10).approved
    unknown_ownership = snapshot(equity=900, cash=700, positions=(Position("SPY", 10, 10),), asset_value=100, valuation_complete=False, positions_complete=False)
    assert not gate.evaluate(OrderIntent("SPY", SignalAction.SELL, 5), unknown_ownership, 10).approved


def test_daily_latch_hwm_and_critical_reset_are_durable_domain_rules():
    service = RiskStateService()
    with pytest.raises(ValueError, match="reconciliation"):
        service.observe(None, equity=1000, session_id="s1", timestamp=NOW)
    state = service.observe(None, equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True).state
    daily = service.observe(state, equity=949, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True).state
    assert daily.mode == RiskMode.DAILY_HALT and daily.daily_latched
    recovered = service.observe(daily, equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True).state
    assert recovered.mode == RiskMode.DAILY_HALT and recovered.high_water_equity == 1000
    new_session = service.observe(recovered, equity=1000, session_id="s2", timestamp=NOW, reconciled=True, valid_session=True).state
    assert new_session.mode == RiskMode.NORMAL and new_session.daily_baseline_equity == 1000
    critical = service.observe(new_session, equity=699, session_id="s2", timestamp=NOW, reconciled=True, valid_session=True).state
    assert critical.mode == RiskMode.CRITICAL_HALT and critical.critical_latched
    with pytest.raises(ValueError):
        service.reset_critical(critical, equity=1000, session_id="s2", timestamp=NOW, operator="ops", reason=" ")
    reset = service.reset_critical(critical, equity=700, session_id="s2", timestamp=NOW, operator="ops@example.com", reason="operator verified reconciliation")
    assert reset.state.mode == RiskMode.NORMAL and reset.state.high_water_equity == 700
    assert reset.state.reset_operator == "ops@example.com"


def test_risk_state_events_are_transactional_and_trigger_events_are_idempotent(tmp_path):
    path = tmp_path / "risk.db"
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{path}", create_schema=True)
    first = repository.evaluate_risk_state(equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    daily = repository.evaluate_risk_state(equity=949, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    repeat = repository.evaluate_risk_state(equity=948, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    assert first.state.mode == RiskMode.NORMAL and daily.state.mode == RiskMode.DAILY_HALT and repeat.state.mode == RiskMode.DAILY_HALT
    with Session(repository.engine) as session:
        events = session.scalars(select(RiskEventRow).order_by(RiskEventRow.created_at)).all()
    assert [event.event_type for event in events] == ["INITIALIZED", "DAILY_HALT"]
    critical = repository.evaluate_risk_state(equity=699, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    assert critical.state.mode == RiskMode.CRITICAL_HALT
    reset = repository.reset_critical_risk(equity=700, cash=600, positions=(Position("SPY", 10, 10),), session_id="s1", timestamp=NOW, operator="ops@example.com", reason="verified operator reset")
    assert reset.state.mode == RiskMode.NORMAL
    assert repository.load_risk_state().high_water_equity == 700
    status = repository.load_status()
    assert status.risk_mode == "NORMAL" and status.trading_health == "healthy" and status.current_equity == 700
    assert status.latest_completed_session == "s1" and status.current_positions == (Position("SPY", 10, 10),)
    with Session(repository.engine) as session:
        keys = session.scalars(select(RiskEventRow.event_key).order_by(RiskEventRow.event_key)).all()
    assert len(keys) == len(set(keys))
    assert any("CRITICAL_HALT_RESET" in key for key in keys)


def test_critical_drawdown_on_new_session_records_critical_event(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'session-critical.db'}", create_schema=True)
    repository.evaluate_risk_state(equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=700, session_id="s2", timestamp=NOW, reconciled=True, valid_session=True)
    with Session(repository.engine) as session:
        events = session.scalars(select(RiskEventRow).order_by(RiskEventRow.created_at)).all()
    assert events[-1].event_type == "CRITICAL_HALT"


def test_risk_rejection_event_is_persisted_with_cycle(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'rejection.db'}", create_schema=True)
    broker = PaperBroker(1000, transaction_cost_bps=0)
    decision = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", (Bar(NOW.date() + timedelta(days=i), 10 + i, 10 + i, 10 + i, 10 + i, 1000) for i in range(4))),
        risk_gate=RiskGate(RiskConfig(max_order_notional=10000), transaction_cost_bps=0), audit_store=repository,
        max_execution_shares=21,
    ).run("SYNTH", NOW)
    assert decision.outcome == "NO_TRADE"
    assert decision.risk_decision.event_type == "POSITION_LIMIT_REJECTED"
    with Session(repository.engine) as session:
        event = session.scalar(select(RiskEventRow).where(RiskEventRow.event_type == "POSITION_LIMIT_REJECTED"))
    assert event is not None and event.event_payload["cycle_id"] == decision.cycle_id


def test_risk_rejected_observation_cycle_is_no_trade(tmp_path):
    broker = PaperBroker(1000, transaction_cost_bps=0)
    decision = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", tuple(Bar(NOW.date() + timedelta(days=i), 10 + i, 10 + i, 10 + i, 10 + i, 1000) for i in range(4))),
        risk_gate=RiskGate(RiskConfig(max_order_notional=10000), transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"),
        execution_mode="observation", max_execution_shares=21,
    ).run("SYNTH", NOW)
    assert decision.signal == SignalAction.BUY
    assert decision.outcome == "NO_TRADE"
    assert decision.risk_decision.event_type == "POSITION_LIMIT_REJECTED"


def test_trader_restart_restores_durable_halt_before_status_is_served(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'restart.db'}", create_schema=True)
    repository.evaluate_risk_state(equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=699, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    broker = PaperBroker(1000, transaction_cost_bps=0)
    cycle = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", tuple(Bar(NOW.date() + timedelta(days=i), 10 + i, 10 + i, 10 + i, 10 + i, 1000) for i in range(4))),
        risk_gate=RiskGate(transaction_cost_bps=0), audit_store=repository, risk_state_store=repository,
    )
    trader = AutonomousTrader(symbol="SYNTH", cycle_service=cycle, clock=FakeClock(NOW), status_store=repository, session_provider=FixtureMarketSessionProvider(()))
    assert trader.status.risk_mode == "CRITICAL_HALT"
    assert trader.status.trading_health == "halted"
    assert repository.load_status().risk_mode == "CRITICAL_HALT"


def test_stale_different_date_cannot_advance_or_reset_session_state(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'stale.db'}", create_schema=True)
    repository.evaluate_risk_state(equity=1000, session_id="2026-01-01", timestamp=NOW, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=949, session_id="2026-01-01", timestamp=NOW, reconciled=True, valid_session=True)
    stale_time = NOW + timedelta(days=10)
    bars = tuple(Bar(NOW.date() + timedelta(days=i), 10 + i, 10 + i, 10 + i, 10 + i, 1000) for i in range(3))
    provider = FixtureMarketDataProvider("SPY", bars, data_timestamp=NOW + timedelta(days=1), max_age=timedelta(days=1))
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=PaperBroker(1000, transaction_cost_bps=0), market_data=provider, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"), risk_state_store=repository)
    decision = cycle.run("SPY", stale_time, reconciled=True, session_id="2026-01-03")
    assert decision.outcome == "NO_TRADE" and decision.risk_decision.mode == "DAILY_HALT"
    state = repository.load_risk_state()
    assert state.session_id == "2026-01-01" and state.mode == RiskMode.DAILY_HALT and state.daily_baseline_equity == 1000
