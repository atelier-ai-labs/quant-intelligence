from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from quant_intelligence.trading.alpaca_adapter import AlpacaTradingAdapter, normalize_status
from quant_intelligence.trading.broker import BrokerOrderStatus
from quant_intelligence.trading.models import OrderIntent, SignalAction


NOW = datetime(2020, 1, 4, 12, tzinfo=timezone.utc)


class Client:
    def __init__(self, remote):
        self.remote = remote
        self.submitted = []

    def get_account(self):
        return SimpleNamespace(cash="125.50", equity="130.50")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="SPY", qty="2", avg_entry_price="60.25")]

    def submit_order(self, request):
        self.submitted.append(request)
        return self.remote

    def get_order_by_id(self, order_id):
        return self.remote

    def get_order_by_client_id(self, client_order_id):
        return self.remote


def remote(status="new"):
    return SimpleNamespace(id="alpaca-1", client_order_id="qi-1", symbol="SPY", side="buy", qty="2", status=status, submitted_at=NOW, filled_qty="0")


def test_adapter_normalizes_account_positions_and_unknown_status():
    adapter = AlpacaTradingAdapter(object(), client=Client(remote("vendor_future_status")), order_request_builder=lambda intent: {"symbol": intent.symbol, "qty": intent.quantity, "side": intent.side.value})
    account = adapter.account()
    assert (account.cash, account.equity) == (125.5, 130.5)
    assert adapter.positions()[0].shares == 2
    assert normalize_status("vendor_future_status") is BrokerOrderStatus.UNKNOWN
    order = adapter.get_order_by_client_id("qi-1")
    assert order is not None and order.status is BrokerOrderStatus.UNKNOWN


def test_pending_new_normalizes_to_submitted():
    assert normalize_status("pending_new") is BrokerOrderStatus.SUBMITTED


def test_adapter_translates_submission_and_normalizes_fill():
    filled = remote("filled")
    filled.filled_qty = "2"
    filled.filled_avg_price = "61.25"
    client = Client(filled)
    adapter = AlpacaTradingAdapter(object(), client=client, order_request_builder=lambda intent: {"symbol": intent.symbol, "qty": intent.quantity, "side": intent.side.value, "client_order_id": intent.client_order_id})
    order, fill = adapter.submit(OrderIntent("SPY", SignalAction.BUY, 2, client_order_id="qi-1"), NOW)
    assert client.submitted == [{"symbol": "SPY", "qty": 2, "side": "BUY", "client_order_id": "qi-1"}]
    assert order.status is BrokerOrderStatus.FILLED
    assert fill is not None and fill.price == 61.25


def test_adapter_rejects_fractional_position_at_boundary():
    client = Client(remote())
    client.get_all_positions = lambda: [SimpleNamespace(symbol="SPY", qty="0.5", avg_entry_price="60")]
    adapter = AlpacaTradingAdapter(object(), client=client)
    with pytest.raises(Exception, match="fractional"):
        adapter.positions()


class CalendarClient:
    def __init__(self, sessions):
        self.sessions = sessions
        self.requests = []

    def get_calendar(self, request):
        self.requests.append(request)
        return self.sessions


def test_calendar_builds_alpaca_request_and_normalizes_normal_session():
    client = CalendarClient(
        [SimpleNamespace(date=date(2024, 1, 3), open=time(9, 30), close=time(16, 0))]
    )
    adapter = AlpacaTradingAdapter(object(), client=client)

    sessions = adapter.calendar(date(2024, 1, 1), date(2024, 1, 5))

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.start == date(2024, 1, 1)
    assert request.end == date(2024, 1, 5)
    assert [(session.session_date, session.opens_at, session.closes_at, session.early_close) for session in sessions] == [
        (
            date(2024, 1, 3),
            datetime(2024, 1, 3, 14, 30, tzinfo=timezone.utc),
            datetime(2024, 1, 3, 21, 0, tzinfo=timezone.utc),
            False,
        )
    ]


def test_calendar_normalizes_early_close_and_preserves_closed_day_omission():
    client = CalendarClient(
        [
            SimpleNamespace(date="2024-07-03", open="09:30", close="13:00"),
            SimpleNamespace(date="2024-07-05", open="09:30", close="16:00"),
        ]
    )
    adapter = AlpacaTradingAdapter(object(), client=client)

    sessions = adapter.calendar(date(2024, 7, 3), date(2024, 7, 5))

    assert [session.session_date for session in sessions] == [date(2024, 7, 3), date(2024, 7, 5)]
    assert sessions[0].closes_at == datetime(2024, 7, 3, 17, 0, tzinfo=timezone.utc)
    assert sessions[0].early_close is True
    assert sessions[1].closes_at == datetime(2024, 7, 5, 20, 0, tzinfo=timezone.utc)
    assert sessions[1].early_close is False
