import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import SignalAction
from quant_intelligence.trading.operations import activity_view, soak_summary
from quant_intelligence.trading.persistence import JsonOperationalRepository, SqlAlchemyOperationalRepository, TradingCycleRow
from quant_intelligence.trading.reconciliation import BrokerReconciliation
from quant_intelligence.trading.reconciliation import ReconciliationResult
from quant_intelligence.trading.risk import RiskConfig, RiskGate
from tests.integration.test_trading import NOW, bars


class CountingBroker(PaperBroker):
    def __init__(self):
        super().__init__(1000, transaction_cost_bps=0)
        self.submit_calls = 0

    def submit_order(self, intent, price, timestamp):
        self.submit_calls += 1
        raise AssertionError("shadow mode must not call submit_order")


def shadow_service(tmp_path, broker=None, risk_gate=None, repository=None):
    broker = broker or CountingBroker()
    repository = repository or JsonOperationalRepository(tmp_path / "audit")
    return TradingCycleService(
        strategy=SmaTrendStrategy(3),
        broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=risk_gate or RiskGate(transaction_cost_bps=0),
        audit_store=repository,
        execution_mode="shadow",
        max_execution_shares=5,
    ), broker, repository


def test_approved_shadow_is_durable_and_never_submits(tmp_path):
    service, broker, repository = shadow_service(tmp_path)
    decision = service.run("SYNTH", NOW)

    assert decision.execution_mode == "shadow"
    assert decision.outcome == "SHADOW_WOULD_SUBMIT"
    assert decision.risk_decision.approved
    assert decision.proposed_order is not None and decision.execution_order is not None
    assert decision.execution_order.quantity == 5
    assert decision.submitted_order is None and decision.fill is None
    assert decision.reconciliation == {"broker_mutation": "none", "reason": "shadow mode; broker submission skipped"}
    assert broker.submit_calls == 0
    restored = repository.get(decision.cycle_id)
    assert restored is not None and restored.execution_mode == "shadow"
    assert restored.outcome == "SHADOW_WOULD_SUBMIT"
    assert repository.pending_reconciliations("SYNTH") == []


def test_risk_blocked_shadow_is_non_executable(tmp_path):
    service, broker, repository = shadow_service(tmp_path, risk_gate=RiskGate(RiskConfig(max_order_notional=1), transaction_cost_bps=0))
    decision = service.run("SYNTH", NOW)

    assert decision.execution_mode == "shadow"
    assert decision.outcome == "NO_TRADE"
    assert not decision.risk_decision.approved
    assert decision.submitted_order is None and decision.fill is None
    assert broker.submit_calls == 0
    assert repository.pending_reconciliations() == []


@pytest.mark.parametrize("repository_factory", [
    lambda tmp_path: JsonOperationalRepository(tmp_path / "json"),
    lambda tmp_path: SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'sql.db'}", create_schema=True),
])
def test_restart_and_reconciliation_ignore_shadow_records(tmp_path, repository_factory):
    repository = repository_factory(tmp_path)
    service, broker, repository = shadow_service(tmp_path, repository=repository)
    decision = service.run("SYNTH", NOW)
    if isinstance(repository, SqlAlchemyOperationalRepository):
        repository.engine.dispose()
        repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'sql.db'}")

    assert repository.get(decision.cycle_id).execution_mode == "shadow"
    assert repository.pending_reconciliations("SYNTH") == []
    assert BrokerReconciliation(broker, tmp_path / "reconciliation", repository=repository).reconcile_pending("SYNTH") == []

    # A later paper-mode service has a different cycle identity; it cannot
    # recover or submit the historical shadow record as pending work.
    assert decision.cycle_id != TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(transaction_cost_bps=0), audit_store=repository,
        execution_mode="paper_execution", max_execution_shares=5,
    )._cycle_id("SYNTH", decision.data_timestamp, decision.market_session)


def test_sql_pending_projection_excludes_shadow_even_if_legacy_row_is_misclassified(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'shadow.db'}", create_schema=True)
    timestamp = datetime(2026, 8, 24, tzinfo=timezone.utc)
    with Session(repository.engine) as session:
        session.add(TradingCycleRow(id="shadow-pending", strategy_name="sma_trend", strategy_version="1", symbol="SPY", market_session="2026-08-24", market_data_timestamp=timestamp, mode="shadow", signal="BUY", outcome="PENDING_SUBMISSION", created_at=timestamp, updated_at=timestamp, strategy_parameters={}, signal_reason="legacy", risk_approved=True, risk_reason="approved", proposed_order={"symbol": "SPY", "side": "BUY", "quantity": 1, "client_order_id": "qi-shadow"}, execution_order={"symbol": "SPY", "side": "BUY", "quantity": 1, "client_order_id": "qi-shadow"}))
        session.commit()
    assert repository.pending_reconciliations("SPY") == []


def test_json_restart_ignores_shadow_reconciliation_payload(tmp_path):
    repository = JsonOperationalRepository(tmp_path / "audit")
    service, broker, repository = shadow_service(tmp_path, repository=repository)
    decision = service.run("SYNTH", NOW)
    result = ReconciliationResult(decision.cycle_id, "SYNTH", decision.execution_order.client_order_id, "MISMATCH", decision.execution_order.quantity, 0, 5, 0, "broker-shadow", NOW)

    # The persistence guard refuses to create a reconciliation record for a
    # shadow decision; the hand-written payload models a pre-existing legacy
    # artifact that must also be ignored after restart.
    repository.save_reconciliation(result)
    reconciliation_dir = tmp_path / "audit" / "reconciliations"
    assert not reconciliation_dir.exists()
    reconciliation_dir.mkdir(parents=True)
    (reconciliation_dir / "legacy-shadow.json").write_text(json.dumps(asdict(result), default=str), encoding="utf-8")

    restarted = JsonOperationalRepository(tmp_path / "audit")
    assert restarted.get(decision.cycle_id).execution_mode == "shadow"
    assert restarted.pending_reconciliations("SYNTH") == []
    assert BrokerReconciliation(broker, tmp_path / "reconciliation", repository=restarted).reconcile_pending("SYNTH") == []


def test_operator_views_label_shadow_mode_and_no_broker_mutation(tmp_path):
    service, _, repository = shadow_service(tmp_path)
    decision = service.run("SYNTH", NOW)
    activity = activity_view(repository)
    record = next(item["record"] for item in activity if item["kind"] == "cycle")
    assert record["execution_mode"] == "shadow"
    assert record["outcome"] == "SHADOW_WOULD_SUBMIT"
    assert record["broker_mutation"] == "none"
    summary = soak_summary(repository)
    assert summary["execution_modes"] == {"shadow": 1}
    assert summary["shadow_would_submit"] == 1
