from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import OrderIntent, SignalAction
from quant_intelligence.trading.persistence import (
    Base,
    OrderRow,
    SqlAlchemyOperationalRepository,
    TradingCycleRow,
    operational_repository,
)
from quant_intelligence.trading.risk import RiskGate
from quant_intelligence.trading.status import OperationalStatus
from tests.integration.test_trading import NOW, bars


def repo(tmp_path):
    return SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'operational.db'}", create_schema=True)


def test_repository_persists_cycle_order_fill_and_position_atomically(tmp_path):
    repository = repo(tmp_path)
    broker = PaperBroker(1000, transaction_cost_bps=0)
    decision = TradingCycleService(
        strategy=SmaTrendStrategy(3),
        broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(transaction_cost_bps=0),
        audit_store=repository,
    ).run("SYNTH", NOW)
    loaded = repository.get(decision.cycle_id)
    assert loaded is not None
    assert loaded.signal == SignalAction.BUY
    assert loaded.fill is not None and loaded.fill.quantity == 76
    assert repository.load_status().current_positions == ()


def test_one_cycle_allows_many_orders_and_identity_modes_are_distinct(tmp_path):
    repository = repo(tmp_path)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    with Session(repository.engine) as session:
        for suffix, mode in (("a", "observation"), ("b", "paper_execution")):
            session.add(TradingCycleRow(id=f"cycle-{suffix}", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session=timestamp.isoformat(), mode=mode, signal="HOLD", outcome="HOLD", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="", risk_approved=True, risk_reason="no order"))
        session.flush()
        session.add_all([
            OrderRow(id="order-a", cycle_id="cycle-a", client_order_id="client-a", symbol="SPY", side="BUY", requested_quantity=1, submitted_quantity=1, filled_quantity=0, status="SUBMITTED", submitted_at=timestamp, updated_at=timestamp),
            OrderRow(id="order-b", cycle_id="cycle-a", client_order_id="client-b", symbol="SPY", side="BUY", requested_quantity=1, submitted_quantity=1, filled_quantity=0, status="SUBMITTED", submitted_at=timestamp, updated_at=timestamp),
        ])
        session.commit()
    with Session(repository.engine) as session:
        assert session.query(OrderRow).filter_by(cycle_id="cycle-a").count() == 2


def test_database_constraints_reject_duplicate_identity_and_invalid_fill_count(tmp_path):
    repository = repo(tmp_path)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    with Session(repository.engine) as session:
        values = dict(strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session=timestamp.isoformat(), mode="observation", signal="HOLD", outcome="HOLD", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="", risk_approved=True, risk_reason="no order")
        session.add(TradingCycleRow(id="cycle-1", **values)); session.commit()
        session.add(TradingCycleRow(id="cycle-2", **values))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(OrderRow(id="order-1", cycle_id="cycle-1", client_order_id="client-1", symbol="SPY", side="BUY", requested_quantity=1, submitted_quantity=1, filled_quantity=2, status="FILLED", submitted_at=timestamp, updated_at=timestamp))
        with pytest.raises(IntegrityError):
            session.commit()


def test_status_round_trip_and_postgres_mode_requires_url(tmp_path, monkeypatch):
    repository = repo(tmp_path)
    status = OperationalStatus(state="running", trading_health="healthy", managed_symbols=("SPY",))
    repository.save_status(status)
    assert repository.load_status().managed_symbols == ("SPY",)
    monkeypatch.setenv("QI_TRADING_PERSISTENCE", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        operational_repository(tmp_path / "audit")


def test_persistence_failure_prevents_external_order_submission():
    class FailingRepository:
        def get(self, cycle_id):
            return None

        def save(self, decision):
            raise RuntimeError("database unavailable")

    broker = PaperBroker(1000, transaction_cost_bps=0)
    service = TradingCycleService(
        strategy=SmaTrendStrategy(3),
        broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(transaction_cost_bps=0),
        audit_store=FailingRepository(),
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.run("SYNTH", NOW)
    assert broker.orders == {}
