from datetime import datetime, timezone

import pytest

from quant_intelligence.trading.broker import BrokerOrderStatus, BrokerUnavailable, ExecutionDisabled
from quant_intelligence.trading.models import Order, Position, PortfolioSnapshot, SignalAction
from quant_intelligence.trading.robinhood_adapter import (
    RobinhoodMCPClient,
    RobinhoodTradingAdapter,
    normalize_status,
)


NOW = datetime(2025, 1, 2, 15, 0, tzinfo=timezone.utc)


class FixtureTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.responses[tool_name]


def adapter(accounts=None, **responses):
    transport = FixtureTransport(
        {
            "get_accounts": {"accounts": accounts or [{"id": "agentic-1", "name": "Agentic"}]},
            "get_portfolio": responses.get("portfolio", {"cash": "1000", "equity": "1100"}),
            "get_equity_positions": responses.get("positions", {"positions": []}),
            "get_equity_orders": responses.get("orders", {"orders": []}),
        }
    )
    return RobinhoodTradingAdapter(client=RobinhoodMCPClient(transport)), transport


def test_multiple_accounts_selects_explicit_agentic_account_without_position_assumption():
    instance, transport = adapter([{"id": "standard", "name": "Standard"}, {"id": "agentic-9", "name": "Agentic"}])

    assert instance.account().cash == 1000
    assert transport.calls[1:] == [
        ("get_portfolio", {"account_id": "agentic-9"}),
    ]


@pytest.mark.parametrize("accounts", [
    [{"id": "a", "name": "Agentic"}, {"id": "b", "name": "Agentic"}],
    [{"id": "a", "name": "Standard"}],
    [{"name": "Agentic"}],
])
def test_missing_or_ambiguous_agentic_account_fails_closed_without_identifier(accounts):
    instance, _ = adapter(accounts)
    with pytest.raises(BrokerUnavailable) as error:
        instance.account()
    assert "Agentic account" in str(error.value)
    assert "account" in str(error.value).lower()


def test_account_portfolio_and_positions_are_normalized_and_zero_is_ignored():
    instance, _ = adapter(
        account={"cash": "1000"},
        portfolio={"cash": "1000", "net_worth": "1100"},
        positions={"positions": [
            {"ticker": "SPY", "quantity": "2", "average_price": "50"},
            {"ticker": "ZERO", "quantity": "0", "average_price": "12"},
        ]},
    )

    assert instance.account().equity == 1100
    assert instance.positions() == (Position("SPY", 2, 50.0),)
    snapshot = instance.portfolio({"SPY": 55.0}, NOW)
    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.asset_value == 110
    assert snapshot.valuation_complete is True


def test_malformed_position_fails_safely():
    instance, _ = adapter(positions={"positions": [{"ticker": "SPY", "quantity": "not-a-number"}]})
    with pytest.raises(BrokerUnavailable, match="invalid equity position"):
        instance.positions()


def test_orders_statuses_partial_fill_and_terminal_states_are_normalized():
    instance, _ = adapter(
        orders={"orders": [
            {"id": "o1", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "4", "average_fill_price": "50", "status": "partial", "created_at": "2025-01-02T14:00:00Z"},
            {"id": "o2", "symbol": "QQQ", "side": "sell", "quantity": "2", "filled_quantity": "2", "filled_avg_price": "400", "state": "filled"},
            {"id": "o3", "symbol": "IWM", "side": "buy", "quantity": "1", "status": "expired"},
            {"id": "o4", "symbol": "DIA", "side": "buy", "quantity": "1", "status": "future_vendor_state"},
        ]}
    )

    orders = instance.order_history()
    assert [order.status for order in orders] == [
        BrokerOrderStatus.PARTIALLY_FILLED,
        BrokerOrderStatus.FILLED,
        BrokerOrderStatus.EXPIRED,
        BrokerOrderStatus.UNKNOWN,
    ]
    assert orders[0].filled_quantity == 4
    assert orders[1].filled_at is not None
    assert all(isinstance(order, Order) for order in orders)
    assert normalize_status("queued") is BrokerOrderStatus.SUBMITTED
    assert normalize_status("future_vendor_state") is BrokerOrderStatus.UNKNOWN


def test_reconciliation_lookup_by_client_id_is_read_only_and_normalized():
    instance, _ = adapter(orders={"orders": [{"id": "o1", "client_order_id": "cycle-1", "symbol": "SPY", "side": "buy", "quantity": "1", "status": "new"}]})

    order = instance.get_order_by_client_id("cycle-1")

    assert isinstance(order, Order)
    assert order.client_order_id == "cycle-1"
    assert instance.get_order_by_client_id("missing") is None


@pytest.mark.parametrize("order", [
    {"id": "filled-no-quantity", "symbol": "SPY", "side": "buy", "quantity": "10", "average_fill_price": "50", "status": "filled"},
    {"id": "filled-invalid-quantity", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "not-a-number", "average_fill_price": "50", "status": "filled"},
    {"id": "filled-partial-quantity", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "5", "average_fill_price": "50", "status": "filled"},
    {"id": "filled-zero-quantity", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "0", "average_fill_price": "50", "status": "filled"},
    {"id": "filled-over-quantity", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "11", "average_fill_price": "50", "status": "filled"},
    {"id": "filled-no-price", "symbol": "SPY", "side": "buy", "quantity": "10", "filled_quantity": "10", "status": "filled"},
])
def test_contradictory_filled_data_is_unknown_not_filled(order):
    instance, _ = adapter(orders={"orders": [order]})

    normalized = instance.order_history()[0]

    assert normalized.status is BrokerOrderStatus.UNKNOWN


def test_filled_quantity_equal_to_requested_quantity_is_filled():
    instance, _ = adapter(orders={"orders": [{
        "id": "filled-exact",
        "symbol": "SPY",
        "side": "buy",
        "quantity": "10",
        "filled_quantity": "10",
        "average_fill_price": "50",
        "status": "filled",
    }]})

    assert instance.order_history()[0].status is BrokerOrderStatus.FILLED


def test_submission_is_mechanically_blocked_and_no_write_tool_exists():
    instance, transport = adapter()
    with pytest.raises(ExecutionDisabled, match="read-only"):
        instance.submit_order(None, 1, NOW)
    assert not any("submit" in tool_name.lower() or "execute" in tool_name.lower() for tool_name, _ in transport.calls)


def test_sensitive_account_identifier_is_absent_from_errors_and_public_values():
    secret_id = "RH-VERY-SENSITIVE-ACCOUNT-123456"
    instance, _ = adapter([{"id": secret_id, "name": "Standard"}])
    with pytest.raises(BrokerUnavailable) as error:
        instance.account()
    assert secret_id not in str(error.value)
    assert secret_id not in repr(instance.discover_accounts())
    assert instance.discover_accounts() == (False,)
    assert all(not isinstance(value, dict) for value in instance.discover_accounts())
