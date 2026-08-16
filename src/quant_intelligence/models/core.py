from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class Order:
    date: date
    symbol: str
    side: str
    quantity: int
    price: float
    reason: str

@dataclass(frozen=True)
class Trade:
    date: date
    symbol: str
    side: str
    quantity: int
    execution_price: float
    gross_notional: float
    transaction_cost: float
    cash_after: float
    equity_after: float
    reason: str

@dataclass(frozen=True)
class PortfolioState:
    date: date
    cash: float
    shares: int
    asset_value: float
    equity: float
    transaction_costs_paid: float
    exposure: float
    desired_position: str

@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity: float
