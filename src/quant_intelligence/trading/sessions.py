"""Market-session boundaries used by the forward paper trader."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime
    early_close: bool = False

    @property
    def decision_at(self) -> datetime:
        return self.closes_at


class MarketSessionProvider(Protocol):
    def latest_completed_session(self, now: datetime, post_close_delay: timedelta) -> MarketSession | None: ...
    def next_decision_time(self, now: datetime, post_close_delay: timedelta) -> datetime | None: ...
    def is_trading_day(self, session_date: date) -> bool: ...


def _session_from_item(item: Any) -> MarketSession:
    session_date = getattr(item, "date", None) or item.get("date")
    opens = getattr(item, "open", None) or item.get("open")
    closes = getattr(item, "close", None) or item.get("close")
    if isinstance(session_date, datetime):
        session_date = session_date.date()
    if isinstance(session_date, str):
        session_date = date.fromisoformat(session_date)
    def normalize(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=NEW_YORK)
        return datetime.combine(session_date, time.fromisoformat(str(value)), tzinfo=NEW_YORK)
    opens_at = normalize(opens).astimezone(UTC)
    closes_at = normalize(closes).astimezone(UTC)
    return MarketSession(session_date, opens_at, closes_at, closes_at.astimezone(NEW_YORK).time() < time(16, 0))


class FixtureMarketSessionProvider:
    """Deterministic calendar boundary for tests and local simulations."""

    def __init__(self, sessions: Sequence[MarketSession]):
        self.sessions = tuple(sorted(sessions, key=lambda item: item.session_date))

    def latest_completed_session(self, now: datetime, post_close_delay: timedelta) -> MarketSession | None:
        current = now.astimezone(UTC)
        eligible = [item for item in self.sessions if item.closes_at + post_close_delay <= current]
        return eligible[-1] if eligible else None

    def next_decision_time(self, now: datetime, post_close_delay: timedelta) -> datetime | None:
        current = now.astimezone(UTC)
        for item in self.sessions:
            decision_at = item.closes_at + post_close_delay
            if decision_at > current:
                return decision_at
        return None

    def is_trading_day(self, session_date: date) -> bool:
        return any(item.session_date == session_date for item in self.sessions)


class AlpacaMarketSessionProvider:
    """Calendar adapter; Alpaca remains isolated from the trading domain."""

    def __init__(self, trading_client: Any):
        self.client = trading_client

    def _calendar(self, now: datetime) -> tuple[MarketSession, ...]:
        current = now.astimezone(NEW_YORK).date()
        try:
            from alpaca.trading.requests import GetCalendarRequest
            items = self.client.get_calendar(GetCalendarRequest(start=current - timedelta(days=14), end=current + timedelta(days=45)))
        except TypeError:
            # Small local fakes may expose the older keyword-shaped boundary.
            try:
                items = self.client.get_calendar(start=current - timedelta(days=14), end=current + timedelta(days=45))
            except Exception as exc:
                raise RuntimeError(f"market calendar unavailable: {exc.__class__.__name__}") from exc
        except ImportError as exc:
            raise RuntimeError("alpaca-py is required for the Alpaca market calendar") from exc
        except Exception as exc:
            raise RuntimeError(f"market calendar unavailable: {exc.__class__.__name__}") from exc
        try:
            return tuple(_session_from_item(item) for item in items or ())
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("market calendar returned invalid session data") from exc

    def latest_completed_session(self, now: datetime, post_close_delay: timedelta) -> MarketSession | None:
        current = now.astimezone(UTC)
        eligible = [item for item in self._calendar(now) if item.closes_at + post_close_delay <= current]
        return sorted(eligible, key=lambda item: item.session_date)[-1] if eligible else None

    def next_decision_time(self, now: datetime, post_close_delay: timedelta) -> datetime | None:
        current = now.astimezone(UTC)
        for item in sorted(self._calendar(now), key=lambda value: value.session_date):
            decision_at = item.closes_at + post_close_delay
            if decision_at > current:
                return decision_at
        return None

    def is_trading_day(self, session_date: date) -> bool:
        anchor = datetime.combine(session_date, time(12), tzinfo=NEW_YORK)
        return any(item.session_date == session_date for item in self._calendar(anchor))
