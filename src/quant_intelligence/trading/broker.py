import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from quant_intelligence.portfolio import BasisPointCostModel
from .models import Fill, Order, OrderIntent, PortfolioSnapshot, Position, SignalAction

class BrokerError(RuntimeError):
    pass

class Broker(Protocol):
    def get_account(self) -> tuple[float, float]: ...
    def get_positions(self) -> tuple[Position, ...]: ...
    def submit_order(self, intent: OrderIntent, price: float, timestamp: datetime) -> tuple[Order, Fill]: ...
    def get_order(self, order_id: str) -> Order | None: ...
    def get_portfolio_snapshot(self, prices: dict[str, float], timestamp: datetime) -> PortfolioSnapshot: ...

class PaperBroker:
    def __init__(self, initial_cash: float, transaction_cost_bps: float = 5.0, state_path: str | Path | None = None):
        if initial_cash < 0: raise ValueError("initial_cash cannot be negative")
        self.cash = initial_cash
        self.cost_model = BasisPointCostModel(transaction_cost_bps)
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.transaction_costs_paid = 0.0
        self.state_path = Path(state_path) if state_path else None
        self._load_state()

    def get_account(self) -> tuple[float, float]:
        return self.cash, self.transaction_costs_paid

    def get_positions(self) -> tuple[Position, ...]:
        return tuple(self.positions.values())

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def submit_order(self, intent: OrderIntent, price: float, timestamp: datetime) -> tuple[Order, Fill]:
        if intent.order_type != "MARKET" or intent.asset_type != "EQUITY": raise BrokerError("unsupported order or asset type")
        if not isinstance(intent.quantity, int) or isinstance(intent.quantity, bool): raise BrokerError("quantity must be a whole number")
        if intent.quantity <= 0: raise BrokerError("quantity must be positive")
        if price <= 0: raise BrokerError("price must be positive")
        gross = intent.quantity * price; cost = self.cost_model.cost(gross)
        current = self.positions.get(intent.symbol, Position(intent.symbol, 0, 0.0))
        if intent.side == SignalAction.BUY:
            if gross + cost > self.cash + 1e-9: raise BrokerError("insufficient cash")
            new_shares = current.shares + intent.quantity
            average = ((current.shares * current.average_price) + gross) / new_shares
            self.cash -= gross + cost
            self.positions[intent.symbol] = Position(intent.symbol, new_shares, average)
        elif intent.side == SignalAction.SELL:
            if intent.quantity > current.shares: raise BrokerError("cannot sell more shares than owned")
            self.cash += gross - cost
            remaining = current.shares - intent.quantity
            if remaining: self.positions[intent.symbol] = Position(intent.symbol, remaining, current.average_price)
            else: self.positions.pop(intent.symbol, None)
        else: raise BrokerError("broker accepts BUY or SELL orders only")
        order = Order(uuid4().hex, intent, timestamp, "FILLED")
        fill = Fill(order.order_id, intent.symbol, intent.side, intent.quantity, price, gross, cost, timestamp)
        self.orders[order.order_id] = order; self.fills.append(fill); self.transaction_costs_paid += cost
        self._save_state()
        return order, fill

    def get_portfolio_snapshot(self, prices: dict[str, float], timestamp: datetime) -> PortfolioSnapshot:
        asset_value = sum(position.shares * prices.get(position.symbol, 0.0) for position in self.positions.values())
        return PortfolioSnapshot(timestamp, self.cash, self.get_positions(), asset_value, self.cash + asset_value, self.transaction_costs_paid)

    def _save_state(self) -> None:
        if self.state_path is None: return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cash": self.cash, "transaction_costs_paid": self.transaction_costs_paid, "positions": [asdict(position) for position in self.positions.values()], "orders": [{"order_id": order.order_id, "intent": {**asdict(order.intent), "side": order.intent.side.value}, "submitted_at": order.submitted_at.isoformat(), "status": order.status} for order in self.orders.values()], "fills": [{**asdict(fill), "side": fill.side.value, "filled_at": fill.filled_at.isoformat()} for fill in self.fills]}
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.is_file(): return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.cash = payload["cash"]; self.transaction_costs_paid = payload["transaction_costs_paid"]
        self.positions = {item["symbol"]: Position(item["symbol"], item["shares"], item["average_price"]) for item in payload["positions"]}
        self.orders = {item["order_id"]: Order(item["order_id"], OrderIntent(item["intent"]["symbol"], SignalAction(item["intent"]["side"]), item["intent"]["quantity"], item["intent"]["order_type"], item["intent"]["asset_type"], item["intent"]["reason"]), datetime.fromisoformat(item["submitted_at"]), item["status"]) for item in payload["orders"]}
        self.fills = [Fill(item["order_id"], item["symbol"], SignalAction(item["side"]), item["quantity"], item["price"], item["gross_notional"], item["transaction_cost"], datetime.fromisoformat(item["filled_at"])) for item in payload["fills"]]
