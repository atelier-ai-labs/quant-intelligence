from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

class SignalAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

@dataclass(frozen=True)
class Signal:
    action: SignalAction
    symbol: str
    strategy: str
    data_timestamp: datetime
    reason: str

@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: SignalAction
    quantity: int
    order_type: str = "MARKET"
    asset_type: str = "EQUITY"
    reason: str = ""

@dataclass(frozen=True)
class Order:
    order_id: str
    intent: OrderIntent
    submitted_at: datetime
    status: str

@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: SignalAction
    quantity: int
    price: float
    gross_notional: float
    transaction_cost: float
    filled_at: datetime

@dataclass(frozen=True)
class Position:
    symbol: str
    shares: int
    average_price: float

@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    positions: tuple[Position, ...]
    asset_value: float
    equity: float
    transaction_costs_paid: float

@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    intent: OrderIntent | None

@dataclass(frozen=True)
class TradingDecision:
    cycle_id: str
    timestamp: datetime
    symbol: str
    strategy: str
    strategy_parameters: dict[str, Any]
    data_timestamp: datetime | None
    signal: SignalAction
    signal_reason: str
    portfolio_before: PortfolioSnapshot | None
    proposed_order: OrderIntent | None
    risk_decision: RiskDecision
    submitted_order: Order | None
    fill: Fill | None
    portfolio_after: PortfolioSnapshot | None
    outcome: str
    error: str | None = None
