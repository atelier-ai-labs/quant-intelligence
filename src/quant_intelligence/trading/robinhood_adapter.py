"""Read-only Robinhood MCP boundary.

Robinhood/MCP response shapes and tool names intentionally stop at this module.
The adapter exposes only Quant Intelligence domain values to its callers and
never constructs an execution request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .broker import (
    BrokerAccount,
    BrokerOrderStatus,
    BrokerUnavailable,
    ExecutionDisabled,
)
from .models import Fill, Order, OrderIntent, Position, PortfolioSnapshot, SignalAction


class RobinhoodMCPTransport(Protocol):
    """Minimal injectable MCP transport; production wiring is intentionally absent."""

    def call(self, tool_name: str, arguments: Mapping[str, Any]) -> Any: ...


class RobinhoodMCPClient:
    """Small MCP client whose only operations are read-side operations."""

    _ACCOUNTS = "get_accounts"
    _PORTFOLIO = "get_portfolio"
    _EQUITY_POSITIONS = "get_equity_positions"
    _EQUITY_ORDERS = "get_equity_orders"

    def __init__(self, transport: RobinhoodMCPTransport):
        self.transport = transport

    def get_accounts(self) -> Any:
        return self.transport.call(self._ACCOUNTS, {})

    def get_portfolio(self, account_id: str) -> Any:
        return self.transport.call(self._PORTFOLIO, {"account_id": account_id})

    def get_equity_positions(self, account_id: str) -> Any:
        return self.transport.call(self._EQUITY_POSITIONS, {"account_id": account_id})

    def get_equity_orders(self, account_id: str) -> Any:
        return self.transport.call(self._EQUITY_ORDERS, {"account_id": account_id})


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _items(response: Any, *keys: str) -> tuple[Any, ...]:
    if response is None:
        return ()
    if isinstance(response, (list, tuple)):
        return tuple(response)
    for key in keys:
        value = _value(response, key)
        if isinstance(value, (list, tuple)):
            return tuple(value)
    return (response,)


def _number(value: Any, *, default: float | None = None) -> float | None:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _whole(value: Any, *, default: int | None = None) -> int | None:
    number = _number(value)
    if number is None or number != int(number):
        return default
    return int(number)


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


def normalize_status(value: Any) -> BrokerOrderStatus:
    """Map known Robinhood states; preserve unknown states as non-terminal UNKNOWN."""
    raw = getattr(value, "value", value)
    text = str(raw or "UNKNOWN").upper().replace("-", "_").replace(" ", "_")
    return BrokerOrderStatus(
        {
            "NEW": "SUBMITTED",
            "PENDING": "SUBMITTED",
            "QUEUED": "SUBMITTED",
            "OPEN": "ACCEPTED",
            "ACCEPTED": "ACCEPTED",
            "CONFIRMED": "ACCEPTED",
            "PARTIAL": "PARTIALLY_FILLED",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCELED",
            "CANCELLED": "CANCELED",
            "REJECTED": "REJECTED",
            "EXPIRED": "EXPIRED",
        }.get(text, "UNKNOWN")
    )


def _side(value: Any) -> SignalAction:
    text = str(getattr(value, "value", value) or "").upper()
    if text.endswith("BUY"):
        return SignalAction.BUY
    if text.endswith("SELL"):
        return SignalAction.SELL
    raise ValueError("missing or invalid order side")


class RobinhoodTradingAdapter:
    """Read-only Robinhood adapter for equity account observation."""

    def __init__(self, *, client: Any):
        self.client = client
        self._account_id: str | None = None

    def discover_accounts(self) -> tuple[bool, ...]:
        """Return account discovery flags without exposing account identifiers."""
        accounts: list[bool] = []
        for remote in _items(self.client.get_accounts(), "accounts", "results"):
            marker = _value(remote, "name", _value(remote, "account_type", _value(remote, "type")))
            if marker is None:
                marker = ""
            accounts.append(str(marker).strip().casefold() == "agentic")
        return tuple(accounts)

    def _select_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        candidates = []
        for remote in _items(self.client.get_accounts(), "accounts", "results"):
            marker_values = (_value(remote, "name"), _value(remote, "account_type"), _value(remote, "type"))
            if any(str(value).strip().casefold() == "agentic" for value in marker_values if value is not None):
                account_id = _value(remote, "id", _value(remote, "account_id"))
                if account_id:
                    candidates.append(str(account_id))
        if len(candidates) != 1:
            raise BrokerUnavailable("Robinhood Agentic account could not be identified unambiguously")
        self._account_id = candidates[0]
        return self._account_id

    def account(self) -> BrokerAccount:
        account_id = self._select_account_id()
        try:
            portfolio = self.client.get_portfolio(account_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Robinhood account unavailable: {exc.__class__.__name__}") from exc
        cash = _number(_value(portfolio, "cash"))
        equity = _number(_value(portfolio, "equity", _value(portfolio, "net_worth")))
        if cash is None or cash < 0 or (equity is not None and equity < 0):
            raise BrokerUnavailable("Robinhood returned invalid account data")
        return BrokerAccount(cash, equity)

    get_account = account

    def positions(self) -> tuple[Position, ...]:
        account_id = self._select_account_id()
        try:
            response = self.client.get_equity_positions(account_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Robinhood positions unavailable: {exc.__class__.__name__}") from exc
        result = []
        for remote in _items(response, "positions", "results"):
            shares = _whole(_value(remote, "shares", _value(remote, "quantity", _value(remote, "qty"))))
            if shares == 0:
                continue
            symbol = _value(remote, "symbol", _value(remote, "ticker"))
            average = _number(_value(remote, "average_price", _value(remote, "avg_entry_price")))
            if not symbol or shares is None or shares < 0 or average is None or average < 0:
                raise BrokerUnavailable("Robinhood returned invalid equity position data")
            result.append(Position(str(symbol), shares, average))
        return tuple(result)

    get_positions = positions

    def portfolio(self, prices: dict[str, float], timestamp: datetime) -> PortfolioSnapshot:
        account = self.account()
        positions = self.positions()
        complete = all(position.symbol in prices and prices[position.symbol] > 0 for position in positions)
        asset_value = sum(position.shares * prices[position.symbol] for position in positions if position.symbol in prices)
        return PortfolioSnapshot(timestamp, account.cash, positions, asset_value, account.equity if account.equity is not None else account.cash + asset_value, 0.0, valuation_complete=complete, positions_complete=True)

    get_portfolio_snapshot = portfolio

    def _normalize_order(self, remote: Any, fallback_timestamp: datetime) -> Order:
        symbol = _value(remote, "symbol", _value(remote, "ticker"))
        quantity = _whole(_value(remote, "quantity", _value(remote, "qty")))
        try:
            side = _side(_value(remote, "side"))
        except ValueError as exc:
            raise BrokerUnavailable("Robinhood returned invalid equity order data") from exc
        if not symbol or quantity is None or quantity <= 0:
            raise BrokerUnavailable("Robinhood returned invalid equity order data")
        submitted = _timestamp(_value(remote, "submitted_at", _value(remote, "created_at")), fallback_timestamp)
        broker_id = str(_value(remote, "id", _value(remote, "order_id", "")) or "")
        client_id = _value(remote, "client_order_id")
        filled_quantity = _whole(_value(remote, "filled_quantity", _value(remote, "filled_qty")), default=0)
        average = _number(_value(remote, "average_fill_price", _value(remote, "filled_avg_price")))
        intent = OrderIntent(str(symbol), side, quantity, client_order_id=str(client_id) if client_id else None)
        status = normalize_status(_value(remote, "status", _value(remote, "state")))
        if status is not BrokerOrderStatus.FILLED and (
            filled_quantity is None or filled_quantity < 0 or filled_quantity > quantity
        ):
            raise BrokerUnavailable("Robinhood returned invalid equity order data")
        if status is BrokerOrderStatus.FILLED and filled_quantity != quantity:
            status = BrokerOrderStatus.UNKNOWN
        elif status is BrokerOrderStatus.FILLED and (average is None or average <= 0):
            status = BrokerOrderStatus.UNKNOWN
        filled_at = _timestamp(_value(remote, "filled_at", _value(remote, "updated_at")), submitted) if filled_quantity else None
        return Order(broker_id or "unknown", intent, submitted, status, intent.client_order_id, broker_id or None, filled_quantity, average, filled_at)

    def order_history(self) -> tuple[Order, ...]:
        account_id = self._select_account_id()
        try:
            response = self.client.get_equity_orders(account_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Robinhood order history unavailable: {exc.__class__.__name__}") from exc
        now = datetime.now(timezone.utc)
        return tuple(self._normalize_order(remote, now) for remote in _items(response, "orders", "results"))

    orders = order_history

    def get_order(self, order_id: str) -> Order | None:
        return next((order for order in self.order_history() if order.broker_order_id == order_id or order.order_id == order_id), None)

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        """Read-only reconciliation lookup over normalized order history."""
        return next((order for order in self.order_history() if order.client_order_id == client_order_id), None)

    def submit_order(self, intent: OrderIntent, price: float, timestamp: datetime) -> tuple[Order, Fill | None]:
        raise ExecutionDisabled("Robinhood integration is strictly read-only")
