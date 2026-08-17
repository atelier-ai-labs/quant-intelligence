import json
from datetime import datetime, timedelta, timezone

import pytest

from quant_intelligence.models import Bar
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import BrokerError, PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.models import OrderIntent, PortfolioSnapshot, Position, SignalAction
from quant_intelligence.trading.risk import RiskConfig, RiskGate
from quant_intelligence.strategies import SmaTrendStrategy

NOW = datetime(2020, 1, 4, 12, tzinfo=timezone.utc)

def bars(closes: list[float], opens: list[float] | None = None) -> list[Bar]:
    opens = opens or closes
    from datetime import date, timedelta
    return [Bar(date(2020, 1, 1) + timedelta(days=i), open_, max(open_, close), min(open_, close), close, 1000) for i, (open_, close) in enumerate(zip(opens, closes))]

def service(tmp_path, data, *, broker=None, risk=None, provider=None):
    broker = broker or PaperBroker(1000, transaction_cost_bps=100)
    provider = provider or FixtureMarketDataProvider("SYNTH", data)
    return TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=risk or RiskGate(transaction_cost_bps=100), audit_store=TradingAuditStore(tmp_path / "audit")), broker

def test_paper_broker_buy_sell_reconciles_with_costs():
    broker = PaperBroker(1000, transaction_cost_bps=100)
    buy, fill = broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 9), 10, NOW)
    assert buy.status == "FILLED"; assert fill.transaction_cost == pytest.approx(0.9)
    assert broker.cash == pytest.approx(909.1)
    sell, sell_fill = broker.submit_order(OrderIntent("SYNTH", SignalAction.SELL, 9), 12, NOW + timedelta(days=1))
    assert sell.status == "FILLED"; assert sell_fill.transaction_cost == pytest.approx(1.08)
    snapshot = broker.get_portfolio_snapshot({"SYNTH": 12}, NOW + timedelta(days=1))
    assert snapshot.positions == (); assert snapshot.cash == pytest.approx(1016.02); assert snapshot.equity == snapshot.cash

def test_paper_broker_state_round_trips(tmp_path):
    state_path = tmp_path / "broker.json"
    broker = PaperBroker(1000, state_path=state_path)
    order, _ = broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 10), 10, NOW)
    restored = PaperBroker(0, state_path=state_path)
    assert restored.cash == pytest.approx(899.95)
    assert restored.get_positions()[0].shares == 10
    assert restored.get_order(order.order_id) is not None
    assert len(restored.fills) == 1

def test_paper_broker_rejects_insufficient_cash_overselling_and_fractional_quantity():
    broker = PaperBroker(10, transaction_cost_bps=0)
    with pytest.raises(BrokerError, match="insufficient cash"): broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 2), 10, NOW)
    with pytest.raises(BrokerError, match="quantity must be positive"): broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 0), 10, NOW)
    with pytest.raises(BrokerError, match="whole number"): broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1.5), 10, NOW)
    broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1), 10, NOW)
    with pytest.raises(BrokerError, match="more shares"): broker.submit_order(OrderIntent("SYNTH", SignalAction.SELL, 2), 10, NOW)

def test_risk_gate_accepts_valid_order_and_rejects_hard_limits():
    snapshot = PortfolioSnapshot(NOW, 1000, (), 0, 1000, 0)
    gate = RiskGate(RiskConfig(max_position_allocation=0.5, max_order_notional=400), transaction_cost_bps=0)
    assert gate.evaluate(OrderIntent("SYNTH", SignalAction.BUY, 20), snapshot, 10).approved
    assert "notional" in gate.evaluate(OrderIntent("SYNTH", SignalAction.BUY, 41), snapshot, 10).reason
    assert "allocation" in gate.evaluate(OrderIntent("SYNTH", SignalAction.BUY, 51), snapshot, 10).reason or "notional" in gate.evaluate(OrderIntent("SYNTH", SignalAction.BUY, 51), snapshot, 10).reason
    assert "positive" in gate.evaluate(OrderIntent("SYNTH", SignalAction.BUY, 0), snapshot, 10).reason
    assert "owned" in gate.evaluate(OrderIntent("SYNTH", SignalAction.SELL, 1), snapshot, 10).reason

def test_buy_cycle_executes_and_persists_audit_record(tmp_path):
    trading, broker = service(tmp_path, bars([10, 11, 12, 13]))
    decision = trading.run("SYNTH", NOW)
    assert decision.signal == SignalAction.BUY; assert decision.outcome == "EXECUTED"
    assert decision.fill is not None; assert decision.portfolio_before.equity == 1000; assert decision.portfolio_after.positions[0].shares == 76
    saved = json.loads(next((tmp_path / "audit").glob("*.json")).read_text())
    assert {"cycle_id", "signal", "portfolio_before", "proposed_order", "risk_decision", "submitted_order", "fill", "portfolio_after", "outcome"} <= saved.keys()
    assert len(broker.orders) == 1

def test_hold_cycle_produces_no_order():
    broker = PaperBroker(1000)
    broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1), 10, NOW)
    trading, broker = service(__import__("pathlib").Path("/tmp"), bars([10, 11, 12, 13]), broker=broker)
    decision = trading.run("SYNTH", NOW)
    assert decision.signal == SignalAction.HOLD; assert decision.outcome == "HOLD"; assert len(broker.orders) == 1

def test_sell_cycle_executes(tmp_path):
    broker = PaperBroker(1000)
    broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 10), 10, NOW)
    trading, broker = service(tmp_path, bars([12, 11, 10, 9], [12, 11, 10, 9]), broker=broker)
    decision = trading.run("SYNTH", NOW)
    assert decision.signal == SignalAction.SELL; assert decision.outcome == "EXECUTED"; assert broker.get_positions() == ()

def test_stale_and_unavailable_data_fail_closed(tmp_path):
    stale = FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13]), data_timestamp=NOW - timedelta(days=2))
    stale_service, stale_broker = service(tmp_path / "stale", bars([10, 11, 12, 13]), provider=stale)
    assert stale_service.run("SYNTH", NOW).outcome == "NO_TRADE"; assert len(stale_broker.orders) == 0
    unavailable = FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13]), unavailable=True)
    unavailable_service, unavailable_broker = service(tmp_path / "unavailable", bars([10, 11, 12, 13]), provider=unavailable)
    assert unavailable_service.run("SYNTH", NOW).outcome == "NO_TRADE"; assert len(unavailable_broker.orders) == 0

def test_invalid_data_and_unknown_broker_state_fail_closed(tmp_path):
    class InvalidProvider:
        def get_completed_bars(self, symbol, now): raise ValueError("invalid completed bars")
        def is_fresh(self, snapshot, now): return False
    invalid_service, invalid_broker = service(tmp_path / "invalid", bars([10, 11, 12, 13]), provider=InvalidProvider())
    assert invalid_service.run("SYNTH", NOW).outcome == "NO_TRADE"; assert len(invalid_broker.orders) == 0

    class UnknownBroker(PaperBroker):
        def get_portfolio_snapshot(self, prices, timestamp): raise RuntimeError("account state unavailable")
    unknown_service, unknown_broker = service(tmp_path / "unknown", bars([10, 11, 12, 13]), broker=UnknownBroker(1000))
    decision = unknown_service.run("SYNTH", NOW)
    assert decision.outcome == "NO_TRADE"; assert "broker state unavailable" in decision.signal_reason; assert len(unknown_broker.orders) == 0

def test_risk_rejection_produces_no_order_and_auditable_reason(tmp_path):
    gate = RiskGate(RiskConfig(max_order_notional=1), transaction_cost_bps=100)
    trading, broker = service(tmp_path, bars([10, 11, 12, 13]), risk=gate)
    decision = trading.run("SYNTH", NOW)
    assert decision.outcome == "NO_TRADE"; assert not decision.risk_decision.approved; assert "notional" in decision.risk_decision.reason; assert len(broker.orders) == 0

def test_same_decision_cycle_is_idempotent(tmp_path):
    trading, broker = service(tmp_path, bars([10, 11, 12, 13]))
    first = trading.run("SYNTH", NOW); second = trading.run("SYNTH", NOW)
    assert first.cycle_id == second.cycle_id; assert first == second; assert len(broker.orders) == 1; assert len(broker.fills) == 1; assert len(list((tmp_path / "audit").glob("*.json"))) == 1
