"""Alpaca adapters at the edge of the trading domain.

The Alpaca SDK is imported lazily so fixture-based research and paper-trader
tests remain deterministic and do not require credentials or network access.
"""

import json
import os
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from quant_intelligence.data.validation import validate_bars
from quant_intelligence.models import Bar
from .broker import BrokerError, BrokerSubmissionUnknown, BrokerUnavailable, ExecutionDisabled
from .market import MarketDataSnapshot, MarketDataUnavailable
from .models import Fill, Order, OrderIntent, PortfolioSnapshot, Position, SignalAction


class AlpacaConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str
    secret_key: str
    paper: bool = True
    execution_enabled: bool = False
    data_feed: str = "iex"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "AlpacaConfig":
        env = environ if environ is not None else os.environ
        api_key = env.get("APCA_API_KEY_ID", "")
        secret_key = env.get("APCA_API_SECRET_KEY", "")
        if not api_key or not secret_key:
            raise AlpacaConfigurationError("APCA_API_KEY_ID and APCA_API_SECRET_KEY are required")
        paper_value = env.get("APCA_PAPER", "").lower()
        if paper_value != "true":
            if paper_value == "false":
                raise AlpacaConfigurationError("refusing to start: Alpaca live mode is disabled for v0.3")
            raise AlpacaConfigurationError("APCA_PAPER=true is required")
        feed = env.get("APCA_DATA_FEED", "iex").lower()
        if feed not in {"iex", "sip", "delayed_sip"}:
            raise AlpacaConfigurationError("APCA_DATA_FEED must be iex, sip, or delayed_sip")
        execution = env.get("APCA_EXECUTION_ENABLED", "false").lower() == "true"
        return cls(api_key, secret_key, True, execution, feed)


def client_order_id_for_cycle(cycle_id: str) -> str:
    """Return a stable Alpaca-safe identifier while retaining the cycle prefix."""
    return f"qi-{cycle_id[:40]}"


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "UNKNOWN").upper().replace(" ", "_")
    return {
        "NEW": "SUBMITTED",
        "ACCEPTED": "ACCEPTED",
        "PENDING_NEW": "SUBMITTED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELED",
        "CANCELLED": "CANCELED",
        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }.get(text, "UNKNOWN")


def _side(value: Any) -> SignalAction:
    text = str(getattr(value, "value", value)).upper()
    return SignalAction.SELL if text.endswith("SELL") else SignalAction.BUY


class AlpacaMarketDataProvider:
    """Completed daily bars from Alpaca's stock historical data client.

    The request end time is the start of the current UTC date and results are
    filtered again by UTC date. This excludes today's incomplete daily candle;
    if fewer than ``window`` completed bars remain, the provider fails closed.
    """

    def __init__(self, symbol: str, window: int, config: AlpacaConfig, *, data_client: Any = None, request_builder: Callable[..., Any] | None = None, max_age: timedelta = timedelta(days=4)):
        if window < 1:
            raise ValueError("window must be positive")
        self.symbol = symbol
        self.window = window
        self.config = config
        self.max_age = max_age
        self._request_builder = request_builder
        self.client = data_client or self._build_client(config)

    @staticmethod
    def _build_client(config: AlpacaConfig) -> Any:
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
        except ImportError as exc:
            raise AlpacaConfigurationError("alpaca-py is required for Alpaca market data") from exc
        return StockHistoricalDataClient(config.api_key, config.secret_key)

    def _request(self, start: datetime, end: datetime) -> Any:
        if self._request_builder:
            return self._request_builder(self.symbol, start, end, self.config.data_feed)
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            kwargs: dict[str, Any] = {"symbol_or_symbols": [self.symbol], "timeframe": TimeFrame.Day, "start": start, "end": end}
            if self.config.data_feed:
                from alpaca.data.enums import DataFeed
                kwargs["feed"] = getattr(DataFeed, self.config.data_feed.upper())
            return StockBarsRequest(**kwargs)
        except ImportError as exc:
            raise AlpacaConfigurationError("alpaca-py is required for Alpaca market data") from exc

    def get_completed_bars(self, symbol: str, now: datetime) -> MarketDataSnapshot:
        if symbol != self.symbol:
            raise MarketDataUnavailable(f"provider is configured for {self.symbol}")
        current = now.astimezone(timezone.utc)
        end = datetime.combine(current.date(), time.min, tzinfo=timezone.utc)
        start = end - timedelta(days=max(self.window * 4, self.window + 14))
        try:
            response = self.client.get_stock_bars(self._request(start, end))
        except Exception as exc:
            raise MarketDataUnavailable(f"Alpaca market data unavailable: {exc.__class__.__name__}") from exc
        series = _value(response, "data", response)
        if isinstance(series, dict):
            series = series.get(symbol, [])
        bars: list[Bar] = []
        for item in series or []:
            timestamp = _value(item, "timestamp")
            if timestamp is None:
                continue
            if isinstance(timestamp, date) and not isinstance(timestamp, datetime):
                bar_date = timestamp
            else:
                parsed = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                bar_date = parsed.astimezone(timezone.utc).date() if parsed.tzinfo else parsed.date()
            if bar_date >= current.date():
                continue
            try:
                bars.append(Bar(bar_date, float(_value(item, "open")), float(_value(item, "high")), float(_value(item, "low")), float(_value(item, "close")), float(_value(item, "volume"))))
            except (TypeError, ValueError) as exc:
                raise MarketDataUnavailable("Alpaca returned invalid daily bar data") from exc
        if len(bars) < self.window:
            raise MarketDataUnavailable(f"only {len(bars)} completed bars available; need {self.window}")
        try:
            normalized = tuple(validate_bars(bars))[-max(self.window * 2, self.window):]
        except ValueError as exc:
            raise MarketDataUnavailable(f"Alpaca returned invalid daily bars: {exc}") from exc
        data_timestamp = datetime.combine(normalized[-1].date, time.min, tzinfo=timezone.utc)
        return MarketDataSnapshot(symbol, normalized, data_timestamp, now)

    def is_fresh(self, snapshot: MarketDataSnapshot, now: datetime) -> bool:
        return now.astimezone(timezone.utc) - snapshot.data_timestamp <= self.max_age


class AlpacaBroker:
    """Thin broker adapter. Alpaca remains authoritative for account state."""

    def __init__(self, config: AlpacaConfig, *, trading_client: Any = None, order_request_builder: Callable[..., Any] | None = None, state_path: str | Path | None = None):
        self.config = config
        if not config.paper:
            raise AlpacaConfigurationError("refusing to start AlpacaBroker outside paper mode")
        self.client = trading_client or self._build_client(config)
        self._order_request_builder = order_request_builder
        self.state_path = Path(state_path) if state_path else None
        self.order_metadata: dict[str, dict[str, Any]] = {}
        self.unresolved_symbols: set[str] = set()
        self._load_metadata()

    @staticmethod
    def _build_client(config: AlpacaConfig) -> Any:
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as exc:
            raise AlpacaConfigurationError("alpaca-py is required for Alpaca paper trading") from exc
        return TradingClient(config.api_key, config.secret_key, paper=True)

    def _account(self) -> Any:
        try:
            return self.client.get_account()
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca account unavailable: {exc.__class__.__name__}") from exc

    def get_account(self) -> tuple[float, float]:
        account = self._account()
        cash = _float(_value(account, "cash"))
        if cash is None:
            raise BrokerUnavailable("Alpaca account did not provide cash")
        return cash, 0.0

    def _account_equity(self) -> float | None:
        return _float(_value(self._account(), "equity"))

    def get_positions(self) -> tuple[Position, ...]:
        try:
            remote = self.client.get_all_positions()
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca positions unavailable: {exc.__class__.__name__}") from exc
        positions: list[Position] = []
        for item in remote or []:
            shares = _float(_value(item, "qty"), 0.0)
            average = _float(_value(item, "avg_entry_price"), 0.0)
            if shares is None or average is None or shares < 0:
                raise BrokerUnavailable("Alpaca returned invalid position data")
            if shares != int(shares):
                raise BrokerError("Alpaca returned a fractional position outside v0.3 scope")
            positions.append(Position(str(_value(item, "symbol")), int(shares), average))
        return tuple(positions)

    def get_portfolio_snapshot(self, prices: dict[str, float], timestamp: datetime) -> PortfolioSnapshot:
        cash, costs = self.get_account()
        positions = self.get_positions()
        asset_value = sum(position.shares * prices.get(position.symbol, 0.0) for position in positions)
        equity = self._account_equity()
        return PortfolioSnapshot(timestamp, cash, positions, asset_value, equity if equity is not None else cash + asset_value, costs)

    def _request(self, intent: OrderIntent) -> Any:
        if self._order_request_builder:
            return self._order_request_builder(intent)
        try:
            from alpaca.trading.enums import OrderSide, TimeInForce
            from alpaca.trading.requests import MarketOrderRequest
            side = OrderSide.BUY if intent.side == SignalAction.BUY else OrderSide.SELL
            return MarketOrderRequest(symbol=intent.symbol, qty=intent.quantity, side=side, time_in_force=TimeInForce.DAY, client_order_id=intent.client_order_id)
        except ImportError as exc:
            raise AlpacaConfigurationError("alpaca-py is required for Alpaca paper trading") from exc

    def _order_from_remote(self, remote: Any, intent: OrderIntent, timestamp: datetime) -> tuple[Order, Fill | None]:
        broker_id = str(_value(remote, "id", ""))
        status = _status(_value(remote, "status"))
        submitted = _value(remote, "submitted_at") or timestamp
        if isinstance(submitted, str):
            submitted = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        filled_quantity = int(_float(_value(remote, "filled_qty"), 0.0) or 0)
        average = _float(_value(remote, "filled_avg_price"))
        filled_at = _value(remote, "filled_at")
        if isinstance(filled_at, str):
            filled_at = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
        order = Order(broker_id or intent.client_order_id or "unknown", intent, submitted, status, intent.client_order_id, broker_id or None, filled_quantity, average, filled_at)
        fill = None
        if filled_quantity > 0 and average is not None:
            fill = Fill(order.order_id, intent.symbol, intent.side, filled_quantity, average, filled_quantity * average, 0.0, filled_at or submitted)
        return order, fill

    def submit_order(self, intent: OrderIntent, price: float, timestamp: datetime) -> tuple[Order, Fill | None]:
        if not self.config.execution_enabled:
            raise ExecutionDisabled("Alpaca paper execution is disabled; observation mode")
        if intent.order_type != "MARKET" or intent.asset_type != "EQUITY":
            raise BrokerError("unsupported Alpaca order or asset type")
        if not isinstance(intent.quantity, int) or isinstance(intent.quantity, bool) or intent.quantity <= 0:
            raise BrokerError("Alpaca requires a positive whole-share quantity")
        if intent.side not in {SignalAction.BUY, SignalAction.SELL}:
            raise BrokerError("Alpaca accepts BUY or SELL only")
        if intent.symbol in self.unresolved_symbols:
            raise BrokerError(f"unresolved Alpaca order ambiguity blocks {intent.symbol}")
        self.order_metadata[intent.client_order_id or intent.cycle_id or intent.symbol] = {"cycle_id": intent.cycle_id, "client_order_id": intent.client_order_id, "symbol": intent.symbol, "side": intent.side.value, "submitted_quantity": intent.quantity, "status": "PENDING_SUBMISSION", "timestamp": timestamp.isoformat()}
        self._save_metadata()
        try:
            remote = self.client.submit_order(self._request(intent))
        except (TimeoutError, ConnectionError) as exc:
            self.unresolved_symbols.add(intent.symbol)
            self._save_metadata()
            raise BrokerSubmissionUnknown("Alpaca submission outcome is unknown; reconcile by client order ID") from exc
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca order submission unavailable: {exc.__class__.__name__}") from exc
        order, fill = self._order_from_remote(remote, intent, timestamp)
        self.order_metadata[intent.client_order_id or order.order_id] = {"cycle_id": intent.cycle_id, "client_order_id": intent.client_order_id, "broker_order_id": order.broker_order_id, "status": order.status, "symbol": intent.symbol, "side": intent.side.value, "submitted_quantity": intent.quantity, "filled_quantity": order.filled_quantity, "average_fill_price": order.average_fill_price, "filled_at": order.filled_at.isoformat() if order.filled_at else None, "timestamp": order.submitted_at.isoformat()}
        self._save_metadata()
        return order, fill

    def preflight(self, symbol: str) -> tuple[float, tuple[Position, ...]]:
        """Prove account/position access and resolve known local order ambiguity."""
        cash, _ = self.get_account()
        positions = self.get_positions()
        for key, metadata in list(self.order_metadata.items()):
            client_id = metadata.get("client_order_id")
            if not client_id or metadata.get("status") in {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "MISSING"}:
                continue
            remote = self.get_order_by_client_id(client_id)
            if remote is None:
                self.unresolved_symbols.add(str(metadata.get("symbol", symbol)))
                self._save_metadata()
                raise BrokerError(f"local Alpaca order {client_id} is unresolved")
            metadata["status"] = remote.status
            metadata["broker_order_id"] = remote.broker_order_id
            metadata["filled_quantity"] = remote.filled_quantity
            metadata["average_fill_price"] = remote.average_fill_price
            if remote.status in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "UNKNOWN"}:
                self.unresolved_symbols.add(str(metadata.get("symbol", symbol)))
            else:
                self.unresolved_symbols.discard(str(metadata.get("symbol", symbol)))
        self._save_metadata()
        if symbol in self.unresolved_symbols:
            raise BrokerError(f"unresolved Alpaca state blocks {symbol}")
        return cash, positions

    def poll_order(self, order: Order, *, attempts: int = 5, interval_seconds: float = 2.0, sleep: Any = time_module.sleep) -> tuple[Order, Fill | None]:
        """Poll only a bounded number of times; unresolved orders are never retried."""
        terminal = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}
        current = order
        for attempt in range(max(1, attempts)):
            if current.status in terminal:
                return current, self._fill_from_order(current)
            if attempt + 1 < max(1, attempts):
                sleep(interval_seconds)
            current = self.get_order(current.broker_order_id or current.order_id) or current
        return current, self._fill_from_order(current)

    @staticmethod
    def _fill_from_order(order: Order) -> Fill | None:
        if order.filled_quantity <= 0 or order.average_fill_price is None:
            return None
        return Fill(order.order_id, order.intent.symbol, order.intent.side, order.filled_quantity, order.average_fill_price, order.filled_quantity * order.average_fill_price, 0.0, order.filled_at or order.submitted_at)

    def get_order(self, order_id: str) -> Order | None:
        try:
            remote = self.client.get_order_by_id(order_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca order lookup unavailable: {exc.__class__.__name__}") from exc
        if remote is None:
            return None
        intent = OrderIntent(str(_value(remote, "symbol")), _side(_value(remote, "side")), int(_float(_value(remote, "qty"), 0) or 0), client_order_id=_value(remote, "client_order_id"))
        return self._order_from_remote(remote, intent, datetime.now(timezone.utc))[0]

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        try:
            remote = self.client.get_order_by_client_id(client_order_id)
        except Exception as exc:
            raise BrokerUnavailable(f"Alpaca client-order lookup unavailable: {exc.__class__.__name__}") from exc
        if remote is None:
            return None
        intent = OrderIntent(str(_value(remote, "symbol")), _side(_value(remote, "side")), int(_float(_value(remote, "qty"), 0) or 0), client_order_id=client_order_id)
        return self._order_from_remote(remote, intent, datetime.now(timezone.utc))[0]

    def clear_unresolved_symbol(self, symbol: str) -> None:
        self.unresolved_symbols.discard(symbol)
        self._save_metadata()

    def is_position_managed(self, symbol: str) -> bool:
        """Only positions with persisted Quant fills are strategy-managed."""
        return any(item.get("symbol") == symbol and item.get("status") in {"FILLED", "PARTIALLY_FILLED"} for item in self.order_metadata.values())

    def _save_metadata(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.order_metadata)
        payload["__unresolved_symbols__"] = sorted(self.unresolved_symbols)
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_metadata(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.unresolved_symbols = set(payload.pop("__unresolved_symbols__", []))
            self.order_metadata = payload
        except (OSError, ValueError, TypeError):
            self.order_metadata = {}
