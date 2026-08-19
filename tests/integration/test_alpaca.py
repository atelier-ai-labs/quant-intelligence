from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.alpaca import AlpacaBroker, AlpacaConfig, AlpacaMarketDataProvider, AlpacaConfigurationError, client_order_id_for_cycle
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import BrokerError, BrokerSubmissionUnknown
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.models import Order, OrderIntent, Position, SignalAction
from quant_intelligence.trading.reconciliation import BrokerReconciliation
from quant_intelligence.trading.risk import RiskGate

NOW = datetime(2020, 1, 4, 12, tzinfo=timezone.utc)


def alpaca_bar(day: int, close: float, **overrides):
    return SimpleNamespace(timestamp=datetime(2020, 1, day, 16, tzinfo=timezone.utc), open=close, high=close, low=close, close=close, volume=1000, **overrides)


class FakeDataClient:
    def __init__(self, bars):
        self.bars = bars
        self.requests = []

    def get_stock_bars(self, request):
        self.requests.append(request)
        return {"data": {"SYNTH": self.bars}}


def provider(bars, window=3, max_age=timedelta(days=4)):
    client = FakeDataClient(bars)
    return AlpacaMarketDataProvider("SYNTH", window, AlpacaConfig("key", "secret"), data_client=client, request_builder=lambda symbol, start, end, feed: {"symbol": symbol, "start": start, "end": end, "feed": feed}, max_age=max_age), client


def test_market_provider_normalizes_completed_daily_bars_and_excludes_today():
    data, client = provider([alpaca_bar(1, 10), alpaca_bar(2, 11), alpaca_bar(3, 12), alpaca_bar(4, 99)])
    snapshot = data.get_completed_bars("SYNTH", NOW)
    assert [bar.date for bar in snapshot.bars] == [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]
    assert snapshot.latest_price == 12
    assert client.requests[0]["feed"] == "iex"
    assert data.is_fresh(snapshot, NOW)


def test_market_provider_rejects_insufficient_and_invalid_data():
    insufficient, _ = provider([alpaca_bar(1, 10), alpaca_bar(2, 11)], window=3)
    with pytest.raises(Exception, match="completed bars"):
        insufficient.get_completed_bars("SYNTH", NOW)
    invalid, _ = provider([alpaca_bar(1, 10), alpaca_bar(2, 11), alpaca_bar(3, -1)], window=3)
    with pytest.raises(Exception, match="invalid"):
        invalid.get_completed_bars("SYNTH", NOW)


def test_config_refuses_missing_credentials_and_live_mode():
    with pytest.raises(AlpacaConfigurationError, match="required"):
        AlpacaConfig.from_env({})
    with pytest.raises(AlpacaConfigurationError, match="live mode"):
        AlpacaConfig.from_env({"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "secret", "APCA_PAPER": "false"})


class FakeTradingClient:
    def __init__(self, remote=None, error=None):
        self.remote = remote
        self.error = error
        self.submitted = []

    def get_account(self):
        return SimpleNamespace(cash="1000", equity="1000")

    def get_all_positions(self):
        return []

    def submit_order(self, request):
        self.submitted.append(request)
        if self.error:
            raise self.error
        return self.remote

    def get_order_by_client_id(self, client_order_id):
        return self.remote

    def get_order_by_id(self, order_id):
        return self.remote


def remote_order(status="new", filled_qty="0", filled_avg_price=None):
    return SimpleNamespace(id="alpaca-order-1", client_order_id="qi-cycle", symbol="SYNTH", side="buy", qty="10", status=status, submitted_at=NOW, filled_qty=filled_qty, filled_avg_price=filled_avg_price)


def build_request(intent):
    return {"symbol": intent.symbol, "qty": intent.quantity, "side": intent.side.value, "client_order_id": intent.client_order_id}


def test_broker_maps_paper_market_order_and_client_id_without_faking_unfilled_fill():
    client = FakeTradingClient(remote_order("new"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    intent = OrderIntent("SYNTH", SignalAction.BUY, 10, client_order_id=client_order_id_for_cycle("a" * 64))
    order, fill = broker.submit_order(intent, 10, NOW)
    assert client.submitted[0]["side"] == "BUY"
    assert client.submitted[0]["client_order_id"] == "qi-" + "a" * 40
    assert order.status == "SUBMITTED"
    assert fill is None
    assert order.broker_order_id == "alpaca-order-1"


def test_broker_maps_filled_order_and_rejects_fractional_or_observation_submission():
    client = FakeTradingClient(remote_order("filled", "10", "10.25"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    order, fill = broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 10, client_order_id="qi-x"), 10, NOW)
    assert order.status == "FILLED" and fill is not None and fill.price == 10.25
    with pytest.raises(Exception, match="whole-share"):
        broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1.5, client_order_id="qi-y"), 10, NOW)
    observe = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=False), trading_client=client, order_request_builder=build_request)
    with pytest.raises(Exception, match="disabled"):
        observe.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-z"), 10, NOW)


def test_one_share_cap_preserves_strategy_quantity_and_uses_actual_fill_price(tmp_path):
    market, _ = provider([alpaca_bar(1, 10), alpaca_bar(2, 11), alpaca_bar(3, 12)])
    client = FakeTradingClient(remote_order("filled", "1", "10.25"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    decision = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=market, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"), execution_mode="paper_execution", max_execution_shares=1).run("SYNTH", NOW)
    assert decision.proposed_order is not None and decision.proposed_order.quantity == 83
    assert decision.execution_order is not None and decision.execution_order.quantity == 1
    assert decision.submitted_order is not None and decision.submitted_order.filled_quantity == 1
    assert decision.fill is not None and decision.fill.price == 10.25


def test_preflight_refuses_unknown_local_order(tmp_path):
    client = FakeTradingClient(error=TimeoutError("timed out"))
    state = tmp_path / "orders.json"
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request, state_path=state)
    with pytest.raises(BrokerSubmissionUnknown):
        broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-unknown"), 10, NOW)
    client.error = None
    client.remote = None
    with pytest.raises(BrokerError, match="unresolved"):
        broker.preflight("SYNTH")


def test_lifecycle_states_and_bounded_polling():
    for state in ("partially_filled", "rejected", "canceled"):
        filled = "1" if state == "partially_filled" else "0"
        client = FakeTradingClient(remote_order(state, filled, "10.25" if filled == "1" else None))
        broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
        order, fill = broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 2, client_order_id=f"qi-{state}"), 10, NOW)
        assert order.status == {"partially_filled": "PARTIALLY_FILLED", "rejected": "REJECTED", "canceled": "CANCELED"}[state]
        assert (fill is not None) is (state == "partially_filled")
    client = FakeTradingClient(remote_order("new"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    order, _ = broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-poll"), 10, NOW)
    sleeps: list[float] = []
    final, fill = broker.poll_order(order, attempts=3, interval_seconds=0, sleep=sleeps.append)
    assert final.status == "SUBMITTED" and fill is None and len(sleeps) == 2


def test_timeout_is_unknown_and_not_retried():
    client = FakeTradingClient(error=TimeoutError("timed out"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    with pytest.raises(BrokerSubmissionUnknown):
        broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-timeout"), 10, NOW)
    assert len(client.submitted) == 1


def test_unresolved_symbol_blocks_new_orders_until_reconciliation(tmp_path):
    client = FakeTradingClient(error=TimeoutError("timed out"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    intent = OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-unknown")
    with pytest.raises(BrokerSubmissionUnknown):
        broker.submit_order(intent, 10, NOW)
    with pytest.raises(BrokerError, match="ambiguity"):
        broker.submit_order(OrderIntent("SYNTH", SignalAction.BUY, 1, client_order_id="qi-next"), 10, NOW)
    client.error = None
    client.remote = remote_order("filled", "1", "10")
    result = BrokerReconciliation(broker, tmp_path).reconcile_order(cycle_id="cycle-unknown", symbol="SYNTH", client_order_id="qi-unknown", expected_quantity=1)
    assert result.status == "FILLED"


def test_observation_cycle_uses_real_adapter_boundary_without_submitting(tmp_path):
    market, _ = provider([alpaca_bar(1, 10), alpaca_bar(2, 11), alpaca_bar(3, 12)])
    client = FakeTradingClient(remote_order("new"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=False), trading_client=client, order_request_builder=build_request)
    decision = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=market, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit")).run("SYNTH", NOW)
    assert decision.outcome == "WOULD_BUY"
    assert client.submitted == []


def test_observation_and_execution_have_distinct_identities_and_execution_is_idempotent(tmp_path):
    market, _ = provider([alpaca_bar(1, 10), alpaca_bar(2, 11), alpaca_bar(3, 12)])
    client = FakeTradingClient(remote_order("filled", "1", "10.25"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    audit = TradingAuditStore(tmp_path / "audit")
    first = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=market, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=audit, execution_mode="paper_execution", max_execution_shares=1).run("SYNTH", NOW)
    second = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=market, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=audit, execution_mode="paper_execution", max_execution_shares=1).run("SYNTH", NOW)
    assert first.cycle_id == second.cycle_id and len(client.submitted) == 1


def test_reconciliation_preserves_missing_and_matching_external_state(tmp_path):
    client = FakeTradingClient(remote_order("filled", "10", "10.25"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    reconciliation = BrokerReconciliation(broker, tmp_path / "reconciliation")
    result = reconciliation.reconcile_order(cycle_id="cycle-1", symbol="SYNTH", client_order_id="qi-cycle", expected_quantity=10)
    assert result.status == "FILLED"
    client.remote = None
    missing = reconciliation.reconcile_order(cycle_id="cycle-2", symbol="SYNTH", client_order_id="qi-missing", expected_quantity=10)
    assert missing.status == "MISSING"
    assert (tmp_path / "reconciliation" / "reconciliation-cycle-2.json").is_file()


def test_reconciliation_preserves_position_mismatch(tmp_path):
    client = FakeTradingClient(remote_order("filled", "1", "10"))
    broker = AlpacaBroker(AlpacaConfig("key", "secret", execution_enabled=True), trading_client=client, order_request_builder=build_request)
    reconciliation = BrokerReconciliation(broker, tmp_path)
    result = reconciliation.reconcile_order(cycle_id="cycle-mismatch", symbol="SYNTH", client_order_id="qi-cycle", expected_quantity=1, expected_position=1)
    assert result.status == "MISMATCH"
    payload = (tmp_path / "reconciliation-cycle-mismatch.json").read_text(encoding="utf-8")
    assert '"expected_position": 1' in payload and '"observed_position": 0' in payload
