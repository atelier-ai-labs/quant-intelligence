"""Buy-and-hold and seeded random-signal baselines for the event study."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Direction = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class EventWindow:
    ticker: str
    as_of: datetime
    entry_date: str
    exit_date: str
    horizon: str
    entry_price: float
    exit_price: float
    raw_return: float


def buy_and_hold_return(window: EventWindow) -> float:
    """Always-long return over the same entry/exit prices as the signal event."""
    return window.raw_return


def signed_signal_return(direction: str, raw: float) -> float | None:
    """Map direction to a signed return. Neutral / unknown => None (excluded)."""
    if direction == "bullish":
        return raw
    if direction == "bearish":
        return -raw
    return None


def random_directions(n_events: int, n_draws: int, seed: int) -> list[list[Direction]]:
    """For each draw, assign a random bullish/bearish direction to every event date.

    Same event dates as the study; direction is randomized. Seeded for reproducibility.
    """
    rng = random.Random(seed)
    choices: tuple[Direction, ...] = ("bullish", "bearish")
    return [[rng.choice(choices) for _ in range(n_events)] for _ in range(n_draws)]


def random_tickers(event_tickers: list[str], allowlist: list[str], n_draws: int, seed: int) -> list[list[str]]:
    """For each draw, remap every event to a random allowlist ticker (same dates conceptually).

    Used as a secondary null: keeps event timing, shuffles the name. Seeded.
    """
    if not allowlist:
        raise ValueError("allowlist must be non-empty")
    rng = random.Random(seed + 17)
    return [[rng.choice(allowlist) for _ in event_tickers] for _ in range(n_draws)]
