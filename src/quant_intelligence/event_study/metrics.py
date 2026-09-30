"""Summary metrics for signal returns vs baselines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def hit_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v > 0) / len(values)


def sharpe_ish(values: list[float]) -> float | None:
    """Simple mean/std of event returns (not annualized). None if undefined."""
    mean = _mean(values)
    std = _std(values)
    if mean is None or std is None or std == 0:
        return None
    return mean / std


@dataclass
class BucketStats:
    label: str
    n: int
    hit_rate: float | None
    mean_return: float | None
    sharpe_ish: float | None
    mean_excess_vs_bh: float | None = None
    mean_excess_vs_random: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n": self.n,
            "hit_rate": self.hit_rate,
            "mean_return": self.mean_return,
            "sharpe_ish": self.sharpe_ish,
            "mean_excess_vs_bh": self.mean_excess_vs_bh,
            "mean_excess_vs_random": self.mean_excess_vs_random,
        }


def summarize(label: str, returns: list[float], *, bh: list[float] | None = None, random_mean: float | None = None) -> BucketStats:
    excess_bh = None
    if bh is not None and len(bh) == len(returns) and returns:
        excess_bh = _mean([r - b for r, b in zip(returns, bh)])
    excess_rand = None
    mean = _mean(returns)
    if mean is not None and random_mean is not None:
        excess_rand = mean - random_mean
    return BucketStats(
        label=label,
        n=len(returns),
        hit_rate=hit_rate(returns),
        mean_return=mean,
        sharpe_ish=sharpe_ish(returns),
        mean_excess_vs_bh=excess_bh,
        mean_excess_vs_random=excess_rand,
    )


def confidence_bucket(confidence: float) -> str:
    if confidence < 0.6:
        return "conf<0.6"
    if confidence < 0.8:
        return "conf[0.6,0.8)"
    return "conf[0.8,1.0]"


@dataclass
class HorizonReport:
    horizon: str
    signal: BucketStats
    buy_and_hold: BucketStats
    random: BucketStats
    by_direction: list[BucketStats] = field(default_factory=list)
    by_confidence: list[BucketStats] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "signal": self.signal.to_dict(),
            "buy_and_hold": self.buy_and_hold.to_dict(),
            "random": self.random.to_dict(),
            "by_direction": [b.to_dict() for b in self.by_direction],
            "by_confidence": [b.to_dict() for b in self.by_confidence],
        }


def format_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def format_num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def markdown_table(reports: list[HorizonReport]) -> str:
    """Compact markdown summary: signal vs B&H vs random per horizon."""
    lines = [
        "| Horizon | Cohort | N | Hit rate | Mean return | Sharpe-ish | Excess vs B&H | Excess vs random |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for report in reports:
        for label, stats in (
            ("signal", report.signal),
            ("buy-and-hold", report.buy_and_hold),
            ("random", report.random),
        ):
            lines.append(
                f"| {report.horizon} | {label} | {stats.n} | {format_pct(stats.hit_rate)} | "
                f"{format_pct(stats.mean_return)} | {format_num(stats.sharpe_ish)} | "
                f"{format_pct(stats.mean_excess_vs_bh)} | {format_pct(stats.mean_excess_vs_random)} |"
            )
        for bucket in report.by_direction + report.by_confidence:
            lines.append(
                f"| {report.horizon} | {bucket.label} | {bucket.n} | {format_pct(bucket.hit_rate)} | "
                f"{format_pct(bucket.mean_return)} | {format_num(bucket.sharpe_ish)} | "
                f"{format_pct(bucket.mean_excess_vs_bh)} | {format_pct(bucket.mean_excess_vs_random)} |"
            )
    return "\n".join(lines)
