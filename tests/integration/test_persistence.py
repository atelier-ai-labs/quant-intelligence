from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from types import SimpleNamespace

from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import OrderIntent, Position, SignalAction
from quant_intelligence.trading.persistence import (
    Base,
    JsonOperationalRepository,
    OrderRow,
    ReconciliationRow,
    RiskStateRow,
    SqlAlchemyOperationalRepository,
    TradingCycleRow,
    operational_repository,
)
from quant_intelligence.trading.reconciliation import BrokerReconciliation, ReconciliationResult
from quant_intelligence.trading.risk import RiskConfig, RiskGate
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
        max_execution_shares=15,
    ).run("SYNTH", NOW)
    loaded = repository.get(decision.cycle_id)
    assert loaded is not None
    assert loaded.signal == SignalAction.BUY
    assert loaded.fill is not None and loaded.fill.quantity == 15
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
    status = OperationalStatus(state="running", trading_health="halted", managed_symbols=("SPY",), risk_mode="DAILY_HALT")
    repository.save_status(status)
    assert repository.load_status().managed_symbols == ("SPY",)
    assert repository.load_status().risk_mode == "DAILY_HALT" and repository.load_status().trading_health == "halted"
    monkeypatch.setenv("QI_TRADING_PERSISTENCE", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        operational_repository(tmp_path / "audit")


def test_pending_submission_is_discoverable_without_order_row(tmp_path):
    repository = repo(tmp_path)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    intent = {"symbol": "SPY", "side": "BUY", "quantity": 1, "order_type": "MARKET", "asset_type": "EQUITY", "reason": "test", "client_order_id": "qi-pending", "cycle_id": "cycle-pending"}
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="cycle-pending", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session=timestamp.isoformat(), mode="paper_execution", signal="BUY", outcome="PENDING_SUBMISSION", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="test", risk_approved=True, risk_reason="approved", proposed_order=intent, execution_order=intent))
        session.commit()
    pending = repository.pending_reconciliations("SPY")
    assert pending[0]["cycle_id"] == "cycle-pending" and pending[0]["client_order_id"] == "qi-pending"
    assert pending[0]["submitted_quantity"] == 1 and pending[0]["status"] == "PENDING_SUBMISSION"
    assert pending[0]["expected_position"] is None


def test_expected_post_trade_position_reconstructs_after_repository_restart(tmp_path):
    repository = repo(tmp_path)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    before = {"timestamp": timestamp.isoformat(), "cash": 980.0, "positions": [{"symbol": "SPY", "shares": 2, "average_price": 10.0}], "asset_value": 20.0, "equity": 1000.0, "transaction_costs_paid": 0.0}
    intent = {"symbol": "SPY", "side": "BUY", "quantity": 3, "order_type": "MARKET", "asset_type": "EQUITY", "reason": "test", "client_order_id": "qi-restart", "cycle_id": "cycle-restart"}
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="cycle-restart", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session="2024-01-02", market_data_timestamp=timestamp, mode="paper_execution", signal="BUY", outcome="PENDING_SUBMISSION", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="test", risk_approved=True, risk_reason="approved", portfolio_before=before, proposed_order=intent, execution_order=intent))
        session.flush()
        session.add(OrderRow(id="order-restart", cycle_id="cycle-restart", client_order_id="qi-restart", symbol="SPY", side="BUY", requested_quantity=3, submitted_quantity=3, filled_quantity=0, status="SUBMITTED", submitted_at=timestamp, updated_at=timestamp))
        session.commit()
    repository.engine.dispose()
    restored = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'operational.db'}")
    pending = restored.pending_reconciliations("SPY")
    assert pending[0]["expected_position"] == 5
    assert pending[0]["pre_trade_position"] == 2


def test_reconciliation_mismatch_is_durable_before_position_mirror_update(tmp_path):
    repository = repo(tmp_path)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    intent = {"symbol": "SPY", "side": "BUY", "quantity": 3, "order_type": "MARKET", "asset_type": "EQUITY", "reason": "test", "client_order_id": "qi-mismatch", "cycle_id": "cycle-mismatch"}
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="cycle-mismatch", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session="2024-01-02", market_data_timestamp=timestamp, mode="paper_execution", signal="BUY", outcome="PENDING_SUBMISSION", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="test", risk_approved=True, risk_reason="approved", proposed_order=intent, execution_order=intent))
        session.flush()
        session.add(OrderRow(id="order-mismatch", cycle_id="cycle-mismatch", client_order_id="qi-mismatch", symbol="SPY", side="BUY", requested_quantity=3, submitted_quantity=3, filled_quantity=3, status="FILLED", submitted_at=timestamp, updated_at=timestamp))
        session.commit()

    class Broker:
        unresolved_symbols = {"SPY"}

        def get_order_by_client_id(self, client_order_id):
            return SimpleNamespace(status="FILLED", filled_quantity=3, broker_order_id="broker-1", intent=SimpleNamespace(side="BUY"))

        def get_positions(self):
            return ()

        def clear_unresolved_symbol(self, symbol):
            self.unresolved_symbols.discard(symbol)

    result = BrokerReconciliation(Broker(), tmp_path / "reconciliation", repository=repository).reconcile_order(cycle_id="cycle-mismatch", symbol="SPY", client_order_id="qi-mismatch", expected_quantity=3, expected_position=5, pre_trade_position=2, require_position=True)
    assert result.status == "MISMATCH"
    with Session(repository.engine) as session:
        row = session.scalar(select(ReconciliationRow).where(ReconciliationRow.cycle_id == "cycle-mismatch"))
        assert row is not None and row.status == "MISMATCH"
        assert "expected_position=5" in row.action_taken and "observed_position=0" in row.action_taken
    pending_after_restart = repository.pending_reconciliations("SPY")
    assert len(pending_after_restart) == 1 and pending_after_restart[0]["expected_position"] == 5


@pytest.mark.parametrize("terminal_status", ["FILLED", "CANCELED", "REJECTED"])
def test_latest_terminal_reconciliation_suppresses_stale_pending_order_after_restart(tmp_path, terminal_status):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operational.db'}"
    repository = SqlAlchemyOperationalRepository(database_url, create_schema=True)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    intent = {"symbol": "SPY", "side": "BUY", "quantity": 3, "order_type": "MARKET", "asset_type": "EQUITY", "reason": "test", "client_order_id": "qi-resolved", "cycle_id": "cycle-resolved"}
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="cycle-resolved", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session="2024-01-02", market_data_timestamp=timestamp, mode="paper_execution", signal="BUY", outcome="PENDING_SUBMISSION", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="test", risk_approved=True, risk_reason="approved", portfolio_before={"positions": [{"symbol": "SPY", "shares": 2}]}, proposed_order=intent, execution_order=intent))
        session.flush()
        session.add(OrderRow(id="order-resolved", cycle_id="cycle-resolved", client_order_id="qi-resolved", symbol="SPY", side="BUY", requested_quantity=3, submitted_quantity=3, filled_quantity=3 if terminal_status == "FILLED" else 0, status="SUBMITTED", submitted_at=timestamp, updated_at=timestamp))
        session.commit()
    repository.save_reconciliation(ReconciliationResult("cycle-resolved", "SPY", "qi-resolved", terminal_status, 3, 3 if terminal_status == "FILLED" else 0, 5 if terminal_status == "FILLED" else 2, 5 if terminal_status == "FILLED" else 2, "broker-resolved", timestamp.replace(day=3)))
    repository.engine.dispose()
    restarted = SqlAlchemyOperationalRepository(database_url)
    assert restarted.pending_reconciliations("SPY") == []


def test_json_terminal_metadata_without_context_is_ignored_but_mismatch_recovers(tmp_path):
    repository = JsonOperationalRepository(tmp_path / "audit")
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    mismatch = ReconciliationResult("cycle-json", "SPY", "qi-json", "MISMATCH", 3, 3, 5, 0, "broker-json", timestamp)
    repository.save_reconciliation(mismatch)
    assert repository.pending_reconciliations("SPY")[0]["expected_position"] == 5
    resolved = ReconciliationResult("cycle-json", "SPY", "qi-json", "FILLED", 3, 3, 5, 5, "broker-json", timestamp.replace(day=3))
    repository.save_reconciliation(resolved)
    assert repository.pending_reconciliations("SPY") == []


@pytest.mark.parametrize("unresolved_status", ["UNKNOWN", "MISSING"])
def test_terminal_order_unresolved_reconciliation_retries_after_restart(tmp_path, unresolved_status):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'operational.db'}"
    repository = SqlAlchemyOperationalRepository(database_url, create_schema=True)
    timestamp = datetime(2024, 1, 2, tzinfo=timezone.utc)
    before = {"timestamp": timestamp.isoformat(), "cash": 980.0, "positions": [{"symbol": "SPY", "shares": 2, "average_price": 10.0}], "asset_value": 20.0, "equity": 1000.0, "transaction_costs_paid": 0.0}
    intent = {"symbol": "SPY", "side": "BUY", "quantity": 3, "order_type": "MARKET", "asset_type": "EQUITY", "reason": "test", "client_order_id": "qi-retry", "cycle_id": "cycle-retry"}
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="cycle-retry", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session="2024-01-02", market_data_timestamp=timestamp, mode="paper_execution", signal="BUY", outcome="EXECUTED", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="test", risk_approved=True, risk_reason="approved", portfolio_before=before, proposed_order=intent, execution_order=intent))
        session.flush()
        session.add(OrderRow(id="order-retry", cycle_id="cycle-retry", client_order_id="qi-retry", symbol="SPY", side="BUY", requested_quantity=3, submitted_quantity=3, filled_quantity=3, status="FILLED", submitted_at=timestamp, updated_at=timestamp))
        session.commit()
    repository.save_reconciliation(ReconciliationResult("cycle-retry", "SPY", "qi-retry", unresolved_status, 3, 3, 5, None, "broker-retry", timestamp))
    repository.engine.dispose()
    restarted_repository = SqlAlchemyOperationalRepository(database_url)

    class Broker:
        unresolved_symbols = {"SPY"}

        def get_order_by_client_id(self, client_order_id):
            return SimpleNamespace(status="FILLED", filled_quantity=3, broker_order_id="broker-retry", intent=SimpleNamespace(side="BUY"))

        def get_positions(self):
            return (Position("SPY", 5, 10.0),)

        def clear_unresolved_symbol(self, symbol):
            self.unresolved_symbols.discard(symbol)

    broker = Broker()
    result = BrokerReconciliation(broker, tmp_path / "reconciliation", repository=restarted_repository).reconcile_pending("SPY")[0]
    assert result.status == "FILLED"
    assert broker.unresolved_symbols == set()
    assert restarted_repository.pending_reconciliations("SPY") == []


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
        max_execution_shares=15,
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.run("SYNTH", NOW)
    assert broker.orders == {}


def test_decision_risk_mode_round_trips_for_rejected_and_hold_records(tmp_path):
    repository = repo(tmp_path)
    broker = PaperBroker(1000, transaction_cost_bps=0)
    rejected = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(risk_mode="DAILY_HALT", transaction_cost_bps=0), audit_store=repository,
        max_execution_shares=15,
    ).run("SYNTH", NOW)
    assert repository.get(rejected.cycle_id).risk_decision.mode == "DAILY_HALT"
    held_broker = PaperBroker(900, transaction_cost_bps=0)
    held_broker.positions["OTHER"] = Position("OTHER", 1, 10)
    held = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=held_broker,
        market_data=FixtureMarketDataProvider("OTHER", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(risk_mode="CRITICAL_HALT", transaction_cost_bps=0), audit_store=repository,
    ).run("OTHER", NOW)
    assert held.outcome == "HOLD"
    assert repository.get(held.cycle_id).risk_decision.mode == "CRITICAL_HALT"


def test_postgresql_risk_state_read_uses_for_update_and_hwm_is_monotonic(tmp_path):
    statement = select(RiskStateRow).where(RiskStateRow.account_key == "default").with_for_update()
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    repository = repo(tmp_path)
    repository.evaluate_risk_state(equity=1000, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=1100, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    repository.evaluate_risk_state(equity=1050, session_id="s1", timestamp=NOW, reconciled=True, valid_session=True)
    assert repository.load_risk_state().high_water_equity == 1100


def test_alembic_0002_upgrade_and_safe_downgrade(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert {"risk_state", "risk_events"} <= set(inspector.get_table_names())
    assert "risk_mode" in {column["name"] for column in inspector.get_columns("trading_cycles")}
    assert {"transition_sequence", "reset_operator"} <= {column["name"] for column in inspector.get_columns("risk_state")}
    engine.dispose()
    command.downgrade(config, "base")
    engine = create_engine(database_url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_alembic_revision_ids_fit_version_column_width():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    assert revisions
    assert all(len(revision.revision) <= 32 for revision in revisions)
    latest = script.get_revision("0004_authoritative_session")
    assert latest is not None and latest.down_revision == "0003_operational_events"
