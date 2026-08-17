from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from quant_intelligence.data.validation import validate_bars
from quant_intelligence.models import Bar

@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str
    bars: tuple[Bar, ...]
    data_timestamp: datetime
    retrieved_at: datetime

    @property
    def latest_price(self) -> float:
        return self.bars[-1].close

class MarketDataProvider(Protocol):
    def get_completed_bars(self, symbol: str, now: datetime) -> MarketDataSnapshot: ...
    def is_fresh(self, snapshot: MarketDataSnapshot, now: datetime) -> bool: ...

class MarketDataUnavailable(RuntimeError):
    pass

class FixtureMarketDataProvider:
    def __init__(self, symbol: str, bars: list[Bar], *, data_timestamp: datetime | None = None, unavailable: bool = False, max_age: timedelta = timedelta(days=1)):
        self.symbol = symbol
        self.bars = tuple(validate_bars(bars))
        self.data_timestamp = data_timestamp or datetime.combine(self.bars[-1].date, datetime.min.time(), tzinfo=timezone.utc)
        self.unavailable = unavailable
        self.max_age = max_age

    def get_completed_bars(self, symbol: str, now: datetime) -> MarketDataSnapshot:
        if self.unavailable or symbol != self.symbol: raise MarketDataUnavailable("market data unavailable")
        return MarketDataSnapshot(symbol, self.bars, self.data_timestamp, now)

    def is_fresh(self, snapshot: MarketDataSnapshot, now: datetime) -> bool:
        return now - snapshot.data_timestamp <= self.max_age
