"""Free daily equity prices with a local CSV cache (Yahoo Finance via yfinance, or fixtures).

Cache layout: data/prices/<TICKER>.csv with columns date,open,high,low,close,volume.
Network fetches are opt-in; tests and CI use fixtures / a pre-populated cache.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_intelligence.models import Bar
from quant_intelligence.data.validation import validate_bars

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
# US regular-session close used for point-in-time entry decisions.
SESSION_CLOSE_ET = time(16, 0)


class PriceError(RuntimeError):
    """Price series unavailable or insufficient for the requested window."""


def _parse_bars_csv(path: Path) -> list[Bar]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise PriceError(f"empty price file: {path}")
    bars = [
        Bar(
            date.fromisoformat(row["date"][:10]),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        )
        for row in rows
    ]
    return validate_bars(bars)


def _write_bars_csv(path: Path, bars: list[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow([bar.date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume])


def fetch_yahoo_bars(symbol: str, start: date, end: date) -> list[Bar]:
    """Download daily bars from Yahoo Finance. Requires the optional `yfinance` package."""
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - exercised when dep missing
        raise PriceError("yfinance is not installed; pip install yfinance or use cached/fixture CSVs") from exc
    # yfinance end is exclusive; bump one day so the last session is included.
    frame = yf.Ticker(symbol).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), auto_adjust=True)
    if frame is None or frame.empty:
        raise PriceError(f"Yahoo Finance returned no bars for {symbol} between {start} and {end}")
    bars: list[Bar] = []
    for idx, row in frame.iterrows():
        bars.append(
            Bar(
                idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10]),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row["Volume"]),
            )
        )
    return validate_bars(bars)


class PriceStore:
    """Local CSV cache of daily closes. Optionally refreshes from Yahoo when allow_network=True."""

    def __init__(self, cache_dir: str | Path = "data/prices", *, allow_network: bool = False):
        self.cache_dir = Path(cache_dir)
        self.allow_network = allow_network
        self._bars: dict[str, list[Bar]] = {}

    def cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.upper()}.csv"

    def load(self, symbol: str, *, start: date | None = None, end: date | None = None) -> list[Bar]:
        symbol = symbol.upper()
        if symbol not in self._bars:
            path = self.cache_path(symbol)
            if path.exists():
                self._bars[symbol] = _parse_bars_csv(path)
            elif self.allow_network:
                fetch_start = start or date(2020, 1, 1)
                fetch_end = end or date.today()
                bars = fetch_yahoo_bars(symbol, fetch_start, fetch_end)
                _write_bars_csv(path, bars)
                self._bars[symbol] = bars
                log.info("cached %s bars for %s under %s", len(bars), symbol, path)
            else:
                raise PriceError(f"no cached prices for {symbol} at {path} (network disabled)")
        bars = self._bars[symbol]
        if start is not None:
            bars = [b for b in bars if b.date >= start]
        if end is not None:
            bars = [b for b in bars if b.date <= end]
        return bars

    def close_on(self, symbol: str, session: date) -> float | None:
        for bar in self.load(symbol):
            if bar.date == session:
                return bar.close
        return None

    def session_index(self, symbol: str) -> dict[date, int]:
        return {bar.date: i for i, bar in enumerate(self.load(symbol))}

    def trading_days(self, symbol: str) -> list[date]:
        return [bar.date for bar in self.load(symbol)]


def session_close_known(as_of: datetime) -> bool:
    """True when the US regular-session close for as_of's calendar date is already public."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    local = as_of.astimezone(ET)
    return local.timetz().replace(tzinfo=None) >= SESSION_CLOSE_ET


def entry_session(as_of: datetime, trading_days: list[date]) -> date:
    """First session whose close is known at/after as_of.

    Rule (applied consistently):
    - Convert as_of to America/New_York.
    - If as_of is on/after 16:00 ET and that calendar date is a trading day in the series,
      use that day's close (already published).
    - Otherwise use the first trading day strictly after as_of's ET calendar date.
    Never uses same-day prices that would not yet be known (no open/intraday lookahead).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if not trading_days:
        raise PriceError("empty trading calendar")
    local = as_of.astimezone(ET)
    day = local.date()
    day_set = set(trading_days)
    if session_close_known(as_of) and day in day_set:
        return day
    for session in trading_days:
        if session > day:
            return session
    raise PriceError(f"no trading session after {day.isoformat()} in price series")


def horizon_session(entry: date, trading_days: list[date], sessions_ahead: int) -> date:
    """Return the close date `sessions_ahead` trading sessions after entry (1 = next day)."""
    if sessions_ahead < 1:
        raise ValueError("sessions_ahead must be >= 1")
    try:
        idx = trading_days.index(entry)
    except ValueError as exc:
        raise PriceError(f"entry session {entry} not in calendar") from exc
    target = idx + sessions_ahead
    if target >= len(trading_days):
        raise PriceError(f"need {sessions_ahead} sessions after {entry}; series ends {trading_days[-1]}")
    return trading_days[target]


def as_of_reference_price(store: PriceStore, symbol: str, as_of: datetime) -> float:
    """Close available to RiskGate at signal time: entry-session close rule above.

    Unblocks the signals CLI `--price` gap for the study path without paid data.
    """
    days = store.trading_days(symbol)
    session = entry_session(as_of, days)
    price = store.close_on(symbol, session)
    if price is None or price <= 0:
        raise PriceError(f"missing close for {symbol} on {session}")
    return price


def raw_return(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        raise PriceError("entry price must be positive")
    return exit_price / entry_price - 1.0
