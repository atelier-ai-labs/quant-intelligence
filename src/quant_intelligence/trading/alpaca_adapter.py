"""Alpaca SDK boundary for the trading domain.

Only this module knows how to construct Alpaca requests or inspect Alpaca
responses.  Its public methods return Quant Intelligence domain values.
"""

from datetime import date, datetime, time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .broker import BrokerAccount, BrokerError, BrokerOrderStatus, BrokerUnavailable
from .models import Fill, Order, OrderIntent, OrderStatus, Position, SignalAction
from .sessions import MarketSession

NEW_YORK = ZoneInfo("America/New_York")


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def normalize_status(value: Any) -> BrokerOrderStatus:
    """Map an Alpaca status value to the canonical QI status vocabulary."""
    raw = getattr(value, "value", value)
    text = str(raw or "UNKNOWN").upper().replace(" ", "_")
    return BrokerOrderStatus({
        "NEW": "SUBMITTED",
        "PENDING_NEW": "SUBMITTED",
        "ACCEPTED": "ACCEPTED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELED",
        "CANCELLED": "CANCELED",
        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }.get(text, "UNKNOWN"))


def _side(value: Any) -> SignalAction:
    text = str(getattr(value, "value", value)).upper()
    return SignalAction.SELL if text.endswith("SELL") else SignalAction.BUY


class AlpacaTradingAdapter:
    """Explicitly constructed adapter around an Alpaca trading client."""

    def __init__(self, config: Any, *, client: Any, order_request_builder: Callable[..., Any] | None = None):
        self.config = config
        self.client = client
        self._order_request_builder = order_request_builder

    def account(self) -> BrokerAccount:
        try:
            remote = self.client.get_account()
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca account unavailable: {exc.__class__.__name__}") from exc
        cash = _float(_value(remote, "cash"))
        equity = _float(_value(remote, "equity"))
        if cash is None:
            raise BrokerUnavailable("Alpaca account did not provide cash")
        return BrokerAccount(cash, equity)

    def calendar(self, start: Any, end: Any) -> tuple[MarketSession, ...]:
        """Return normalized calendar sessions, never Alpaca calendar objects."""
        try:
            from alpaca.trading.requests import GetCalendarRequest
            remote = self.client.get_calendar(GetCalendarRequest(start=start, end=end))
        except TypeError:
            try:
                remote = self.client.get_calendar(start=start, end=end)
            except Exception as exc:
                raise RuntimeError(f"market calendar unavailable: {exc.__class__.__name__}") from exc
        except ImportError as exc:
            raise RuntimeError("alpaca-py is required for the Alpaca market calendar") from exc
        except Exception as exc:
            raise RuntimeError(f"market calendar unavailable: {exc.__class__.__name__}") from exc
        try:
            return tuple(_session_from_item(item) for item in remote or ())
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("market calendar returned invalid session data") from exc

    def positions(self) -> tuple[Position, ...]:
        try:
            remote_positions = self.client.get_all_positions()
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca positions unavailable: {exc.__class__.__name__}") from exc
        positions: list[Position] = []
        for remote in remote_positions or ():
            shares = _float(_value(remote, "qty"), 0.0)
            average = _float(_value(remote, "avg_entry_price"), 0.0)
            if shares is None or average is None or shares < 0:
                raise BrokerUnavailable("Alpaca returned invalid position data")
            if shares != int(shares):
                raise BrokerError("Alpaca returned a fractional position outside v0.3 scope")
            symbol = _value(remote, "symbol")
            if not symbol:
                raise BrokerUnavailable("Alpaca returned a position without a symbol")
            positions.append(Position(str(symbol), int(shares), average))
        return tuple(positions)

    def order_request(self, intent: OrderIntent) -> Any:
        if self._order_request_builder:
            return self._order_request_builder(intent)
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
            side = OrderSide.BUY if intent.side == SignalAction.BUY else OrderSide.SELL
            return MarketOrderRequest(symbol=intent.symbol, qty=intent.quantity, side=side, time_in_force=TimeInForce.DAY, client_order_id=intent.client_order_id)
        except ImportError as exc:
            raise RuntimeError("alpaca-py is required for Alpaca paper trading") from exc

    def submit(self, intent: OrderIntent, timestamp: datetime) -> tuple[Order, Fill | None]:
        try:
            remote = self.client.submit_order(self.order_request(intent))
        except Exception as exc:
            raise exc
        return self.normalize_order(remote, intent, timestamp)

    def get_order(self, order_id: str) -> Order | None:
        try:
            remote = self.client.get_order_by_id(order_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca order lookup unavailable: {exc.__class__.__name__}") from exc
        if remote is None:
            return None
        intent = self._intent(remote, _value(remote, "client_order_id"))
        return self.normalize_order(remote, intent, datetime.now(timezone.utc))[0]

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        try:
            remote = self.client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca client-order lookup unavailable: {exc.__class__.__name__}") from exc
        if remote is None:
            return None
        return self.normalize_order(remote, self._intent(remote, client_order_id), datetime.now(timezone.utc))[0]

    def _intent(self, remote: Any, client_order_id: str | None) -> OrderIntent:
        return OrderIntent(str(_value(remote, "symbol")), _side(_value(remote, "side")), int(_float(_value(remote, "qty"), 0) or 0), client_order_id=client_order_id)

    def normalize_order(self, remote: Any, intent: OrderIntent, timestamp: datetime) -> tuple[Order, Fill | None]:
        broker_id = str(_value(remote, "id", ""))
        submitted = _value(remote, "submitted_at") or timestamp
        filled_at = _value(remote, "filled_at")
        if isinstance(submitted, str):
            submitted = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        if isinstance(filled_at, str):
            filled_at = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
        filled_quantity = int(_float(_value(remote, "filled_qty"), 0.0) or 0)
        average = _float(_value(remote, "filled_avg_price"))
        order = Order(broker_id or intent.client_order_id or "unknown", intent, submitted, OrderStatus(normalize_status(_value(remote, "status"))), intent.client_order_id, broker_id or None, filled_quantity, average, filled_at)
        fill = None
        if filled_quantity > 0 and average is not None:
            fill = Fill(order.order_id, intent.symbol, intent.side, filled_quantity, average, filled_quantity * average, 0.0, filled_at or submitted)
        return order, fill


def _session_from_item(item: Any) -> MarketSession:
    session_date = _value(item, "date")
    opens = _value(item, "open")
    closes = _value(item, "close")
    if isinstance(session_date, datetime):
        session_date = session_date.date()
    if isinstance(session_date, str):
        session_date = date.fromisoformat(session_date)

    def normalize(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=NEW_YORK)
        return datetime.combine(session_date, time.fromisoformat(str(value)), tzinfo=NEW_YORK)

    opens_at = normalize(opens).astimezone(timezone.utc)
    closes_at = normalize(closes).astimezone(timezone.utc)
    return MarketSession(session_date, opens_at, closes_at, closes_at.astimezone(NEW_YORK).time() < time(16, 0))
