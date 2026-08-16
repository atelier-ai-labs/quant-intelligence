from collections.abc import Iterable
from math import isfinite
from quant_intelligence.models import Bar

class DataValidationError(ValueError):
    pass

def validate_bars(bars: Iterable[Bar]) -> list[Bar]:
    result = list(bars)
    if not result: raise DataValidationError("market data is empty")
    previous = None
    for bar in result:
        if previous is not None and bar.date <= previous: raise DataValidationError("dates must be strictly increasing with no duplicates")
        previous = bar.date
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if any(not isfinite(value) for value in values): raise DataValidationError(f"missing or non-finite OHLCV value on {bar.date}")
        if any(value <= 0 for value in (bar.open, bar.high, bar.low, bar.close)): raise DataValidationError(f"non-positive price on {bar.date}")
        if bar.volume < 0: raise DataValidationError(f"negative volume on {bar.date}")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close) or bar.low > bar.high: raise DataValidationError(f"malformed OHLC bar on {bar.date}")
    return result
