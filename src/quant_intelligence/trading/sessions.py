"""Market-session boundaries used by the forward paper trader."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol, Sequence
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

    def __init__(self, calendar_provider: object):
        self.calendar_provider = calendar_provider

    def _calendar(self, now: datetime) -> tuple[MarketSession, ...]:
        current = now.astimezone(NEW_YORK).date()
        calendar = getattr(self.calendar_provider, "calendar", None)
        if not callable(calendar):
            raise RuntimeError("normalized market calendar provider is unavailable")
        return tuple(calendar(current - timedelta(days=14), current + timedelta(days=45)))

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
