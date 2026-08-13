from collections.abc import Iterable, Protocol
from datetime import date
from quant_intelligence.models import Bar

class MarketDataProvider(Protocol):
    def get_daily_bars(self, symbol: str, start: date | None = None, end: date | None = None) -> Iterable[Bar]: ...
