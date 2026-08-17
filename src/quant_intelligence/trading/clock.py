from datetime import datetime, timedelta, timezone
from typing import Protocol

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

class FakeClock:
    def __init__(self, current: datetime):
        self._current = current

    def now(self) -> datetime:
        return self._current

    def advance(self, amount: timedelta) -> datetime:
        self._current += amount
        return self._current

    def set(self, current: datetime) -> None:
        self._current = current
