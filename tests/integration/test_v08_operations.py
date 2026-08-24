from datetime import datetime, timezone

from quant_intelligence.trading.models import RiskDecision, SignalAction, TradingDecision
from quant_intelligence.trading.operations import activity_view, soak_summary, status_view
from quant_intelligence.trading.persistence import SqlAlchemyOperationalRepository
from quant_intelligence.trading.status import OperationalStatus


NOW = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def decision(*, cycle_id: str, outcome: str, signal: SignalAction = SignalAction.HOLD, timestamp: datetime = NOW) -> TradingDecision:
    return TradingDecision(
        cycle_id=cycle_id,
        timestamp=timestamp,
        symbol="SPY",
        strategy="sma-trend",
        strategy_parameters={"window": 200},
        data_timestamp=timestamp,
        signal=signal,
        signal_reason="completed bar",
        portfolio_before=None,
        proposed_order=None,
        risk_decision=RiskDecision(True, "no order required", None),
        submitted_order=None,
        fill=None,
        portfolio_after=None,
        outcome=outcome,
    )


def test_operator_status_preserves_unknown_and_halt_state(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'status.db'}", create_schema=True)
    repository.save_status(OperationalStatus(trading_health="unknown", risk_mode="CRITICAL_HALT", halt_reason="broker ambiguity"))
    report = status_view(repository, configured_symbols=("SPY",))
    assert report["trading_health"] == "UNKNOWN"
    assert report["risk_mode"] == "CRITICAL_HALT"
    assert report["halt_reason"] == "broker ambiguity"
    assert report["configured_symbols"] == ["SPY"]


def test_activity_is_bounded_newest_first_and_read_only(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'activity.db'}", create_schema=True)
    repository.save_decision(decision(cycle_id="cycle-1", outcome="NO_ORDER"))
    before = len(repository.list())
    activity = activity_view(repository, limit=1)
    assert len(activity) == 1
    assert activity[0]["kind"] == "cycle"
    assert len(repository.list()) == before


def test_soak_summary_reports_decisions_and_zero_idempotency_violations(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'summary.db'}", create_schema=True)
    repository.save_decision(decision(cycle_id="cycle-1", outcome="NO_ORDER"))
    repository.save_decision(decision(cycle_id="cycle-2", outcome="NO_TRADE", signal=SignalAction.BUY, timestamp=NOW.replace(day=25, hour=21)))
    summary = soak_summary(repository, since=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert summary["cycles"] == 2
    assert summary["outcomes"] == {"NO_ORDER": 1, "NO_TRADE": 1}
    assert summary["signals"]["HOLD"] == 1
    assert summary["duplicate_cycle_violations"] == 0


def test_lifecycle_events_are_append_only_and_deduplicated_by_explicit_key(tmp_path):
    repository = SqlAlchemyOperationalRepository(f"sqlite+pysqlite:///{tmp_path / 'events.db'}", create_schema=True)
    repository.record_operational_event("SERVICE_STARTING", timestamp=NOW, event_key="start-1")
    repository.record_operational_event("SERVICE_STARTING", timestamp=NOW, event_key="start-1")
    events = repository.list_operational_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "SERVICE_STARTING"
