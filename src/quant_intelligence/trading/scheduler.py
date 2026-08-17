from dataclasses import dataclass
from datetime import datetime, timedelta

@dataclass
class IntervalScheduler:
    interval: timedelta
    next_run_at: datetime

    def __post_init__(self) -> None:
        if self.interval <= timedelta(0): raise ValueError("interval must be positive")

    def consume_due(self, now: datetime) -> datetime | None:
        if now < self.next_run_at: return None
        scheduled = self.next_run_at
        self.next_run_at += self.interval
        return scheduled
