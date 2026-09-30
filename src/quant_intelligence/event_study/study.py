"""Orchestrate the multi-ticker point-in-time event study."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from quant_intelligence.signals.schema import RiskSignal

from .baselines import random_directions, signed_signal_return
from .metrics import BucketStats, HorizonReport, confidence_bucket, markdown_table, summarize
from .prices import PriceError, PriceStore, entry_session, horizon_session, raw_return

HorizonName = Literal["next_day", "next_week", "next_month"]
DEFAULT_HORIZONS: dict[HorizonName, int] = {"next_day": 1, "next_week": 5, "next_month": 21}
DEFAULT_RANDOM_DRAWS = 500
DEFAULT_RANDOM_SEED = 42
DEFAULT_MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class EventStudyConfig:
    horizons: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_HORIZONS))
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    random_draws: int = DEFAULT_RANDOM_DRAWS
    random_seed: int = DEFAULT_RANDOM_SEED
    actionable_only: bool = True


@dataclass
class EventRow:
    ticker: str
    as_of: datetime
    direction: str
    confidence: float
    status: str
    entry_date: str
    entry_price: float
    exits: dict[str, dict[str, Any]]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of.isoformat(),
            "direction": self.direction,
            "confidence": self.confidence,
            "status": self.status,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "exits": self.exits,
            "error": self.error,
        }


@dataclass
class EventStudyResult:
    config: EventStudyConfig
    events: list[EventRow]
    reports: list[HorizonReport]
    gate: dict[str, Any]
    price_source: str
    mode: str
    notes: list[str] = field(default_factory=list)

    def markdown_summary(self) -> str:
        return markdown_table(self.reports)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "price_source": self.price_source,
            "config": {
                "horizons": self.config.horizons,
                "min_confidence": self.config.min_confidence,
                "random_draws": self.config.random_draws,
                "random_seed": self.config.random_seed,
                "actionable_only": self.config.actionable_only,
            },
            "events": [e.to_dict() for e in self.events],
            "reports": [r.to_dict() for r in self.reports],
            "gate": self.gate,
            "notes": self.notes,
            "markdown_summary": self.markdown_summary(),
        }


def _is_actionable(signal: RiskSignal, min_confidence: float) -> bool:
    return (
        signal.status == "ok"
        and signal.direction in {"bullish", "bearish"}
        and signal.confidence >= min_confidence
    )


def _build_event(signal: RiskSignal, store: PriceStore, horizons: dict[str, int]) -> EventRow:
    days = store.trading_days(signal.ticker)
    entry = entry_session(signal.as_of, days)
    entry_price = store.close_on(signal.ticker, entry)
    if entry_price is None or entry_price <= 0:
        raise PriceError(f"missing entry close for {signal.ticker} on {entry}")
    exits: dict[str, dict[str, Any]] = {}
    for name, sessions in horizons.items():
        try:
            exit_day = horizon_session(entry, days, sessions)
            exit_price = store.close_on(signal.ticker, exit_day)
            if exit_price is None or exit_price <= 0:
                raise PriceError(f"missing exit close for {signal.ticker} on {exit_day}")
            exits[name] = {
                "exit_date": exit_day.isoformat(),
                "exit_price": exit_price,
                "raw_return": raw_return(entry_price, exit_price),
                "sessions_ahead": sessions,
            }
        except PriceError as exc:
            exits[name] = {"error": str(exc)}
    return EventRow(
        ticker=signal.ticker,
        as_of=signal.as_of,
        direction=signal.direction,
        confidence=signal.confidence,
        status=signal.status,
        entry_date=entry.isoformat(),
        entry_price=entry_price,
        exits=exits,
    )


def _cohort(events: list[EventRow], config: EventStudyConfig) -> list[EventRow]:
    if not config.actionable_only:
        return [e for e in events if e.error is None]
    return [
        e
        for e in events
        if e.error is None
        and e.status == "ok"
        and e.direction in {"bullish", "bearish"}
        and e.confidence >= config.min_confidence
    ]


def _horizon_report(name: str, cohort: list[EventRow], config: EventStudyConfig) -> HorizonReport:
    usable = [e for e in cohort if name in e.exits and "raw_return" in e.exits[name]]
    signal_returns: list[float] = []
    bh_returns: list[float] = []
    directions: list[str] = []
    confidences: list[float] = []
    for event in usable:
        raw = float(event.exits[name]["raw_return"])
        signed = signed_signal_return(event.direction, raw)
        if signed is None:
            continue
        signal_returns.append(signed)
        bh_returns.append(raw)
        directions.append(event.direction)
        confidences.append(event.confidence)

    draw_means: list[float] = []
    if signal_returns:
        draws = random_directions(len(signal_returns), config.random_draws, config.random_seed)
        raws = bh_returns
        for draw in draws:
            vals = []
            for direction, raw in zip(draw, raws):
                signed = signed_signal_return(direction, raw)
                if signed is not None:
                    vals.append(signed)
            if vals:
                draw_means.append(sum(vals) / len(vals))
    random_mean = sum(draw_means) / len(draw_means) if draw_means else None
    random_hit = None
    if draw_means:
        hits = []
        draws = random_directions(len(signal_returns), config.random_draws, config.random_seed)
        raws = bh_returns
        for draw in draws:
            signed_vals = [signed_signal_return(d, r) for d, r in zip(draw, raws)]
            signed_vals = [v for v in signed_vals if v is not None]
            if signed_vals:
                hits.append(sum(1 for v in signed_vals if v > 0) / len(signed_vals))
        random_hit = sum(hits) / len(hits) if hits else None

    signal_stats = summarize("signal", signal_returns, bh=bh_returns, random_mean=random_mean)
    bh_stats = summarize("buy-and-hold", bh_returns, bh=bh_returns, random_mean=random_mean)
    random_stats = BucketStats(
        label="random",
        n=config.random_draws if signal_returns else 0,
        hit_rate=random_hit,
        mean_return=random_mean,
        sharpe_ish=None,
        mean_excess_vs_bh=(random_mean - (sum(bh_returns) / len(bh_returns))) if (random_mean is not None and bh_returns) else None,
        mean_excess_vs_random=0.0 if random_mean is not None else None,
    )

    by_direction: list[BucketStats] = []
    for direction in ("bullish", "bearish"):
        idxs = [i for i, d in enumerate(directions) if d == direction]
        if not idxs:
            continue
        by_direction.append(
            summarize(
                f"signal:{direction}",
                [signal_returns[i] for i in idxs],
                bh=[bh_returns[i] for i in idxs],
                random_mean=random_mean,
            )
        )

    by_confidence: list[BucketStats] = []
    buckets = sorted({confidence_bucket(c) for c in confidences})
    for label in buckets:
        idxs = [i for i, c in enumerate(confidences) if confidence_bucket(c) == label]
        by_confidence.append(
            summarize(
                f"signal:{label}",
                [signal_returns[i] for i in idxs],
                bh=[bh_returns[i] for i in idxs],
                random_mean=random_mean,
            )
        )

    return HorizonReport(
        horizon=name,
        signal=signal_stats,
        buy_and_hold=bh_stats,
        random=random_stats,
        by_direction=by_direction,
        by_confidence=by_confidence,
    )


def decide_gate(reports: list[HorizonReport], *, min_events: int = 8) -> dict[str, Any]:
    """GO / conditional GO / NO-GO.

    GO only if the active signal cohort beats BOTH buy-and-hold and random on a primary
    window (next_day or next_week) by a clear margin, with enough events.
    Default to NO-GO when uncertain — this portfolio piece is about honest evals.
    """
    primary = [r for r in reports if r.horizon in {"next_day", "next_week"}]
    rationale: list[str] = []
    clear_wins: list[str] = []
    conditional: list[str] = []

    for report in primary:
        n = report.signal.n
        if n < min_events:
            rationale.append(
                f"{report.horizon}: only {n} actionable events (need >={min_events} for a full GO); sample too small to claim edge"
            )
            continue
        sig_mean = report.signal.mean_return
        bh_mean = report.buy_and_hold.mean_return
        rand_mean = report.random.mean_return
        if sig_mean is None or bh_mean is None or rand_mean is None:
            rationale.append(f"{report.horizon}: incomplete metrics")
            continue
        margin = 0.005
        beats_bh = sig_mean > bh_mean + margin
        beats_rand = sig_mean > rand_mean + margin
        if beats_bh and beats_rand:
            clear_wins.append(
                f"{report.horizon}: signal mean {sig_mean:.4f} > B&H {bh_mean:.4f} and random {rand_mean:.4f} (n={n})"
            )
        else:
            rationale.append(
                f"{report.horizon}: signal mean {sig_mean:.4f} vs B&H {bh_mean:.4f} vs random {rand_mean:.4f} (n={n}) — no clear edge"
            )

        for bucket in report.by_direction + report.by_confidence:
            if bucket.n < max(5, min_events // 2):
                continue
            if (
                bucket.mean_return is not None
                and bh_mean is not None
                and rand_mean is not None
                and bucket.mean_return > bh_mean + margin
                and bucket.mean_return > rand_mean + margin
            ):
                conditional.append(
                    f"{report.horizon}/{bucket.label}: mean {bucket.mean_return:.4f} beats baselines (n={bucket.n})"
                )

    if clear_wins:
        decision = "GO"
        rationale = clear_wins + rationale
    elif conditional:
        decision = "CONDITIONAL_GO"
        rationale = conditional + rationale
    else:
        decision = "NO-GO"
        if not rationale:
            rationale.append("no primary-window evidence of edge over buy-and-hold and random")

    return {
        "decision": decision,
        "min_events_for_go": min_events,
        "clear_margin_abs": 0.005,
        "rationale": rationale,
        "primary_windows": [r.horizon for r in primary],
    }


def run_event_study(
    signals: list[RiskSignal],
    store: PriceStore,
    *,
    config: EventStudyConfig | None = None,
    mode: str = "replay",
    price_source: str = "yahoo-cache",
) -> EventStudyResult:
    config = config or EventStudyConfig()
    events: list[EventRow] = []
    notes: list[str] = []
    for signal in signals:
        try:
            events.append(_build_event(signal, store, config.horizons))
        except (PriceError, ValueError) as exc:
            events.append(
                EventRow(
                    ticker=signal.ticker,
                    as_of=signal.as_of,
                    direction=signal.direction,
                    confidence=signal.confidence,
                    status=signal.status,
                    entry_date="",
                    entry_price=0.0,
                    exits={},
                    error=str(exc),
                )
            )
            notes.append(f"{signal.ticker} @ {signal.as_of.isoformat()}: {exc}")

    cohort = _cohort(events, config)
    if not cohort:
        notes.append("no actionable signals in cohort (directional + confidence threshold)")

    reports = [_horizon_report(name, cohort, config) for name in config.horizons]
    gate = decide_gate(reports)
    notes.append(
        f"entry rule: as-of close if as_of >= 16:00 ET on a trading day; else next session close. "
        f"horizons: {config.horizons}. random draws={config.random_draws} seed={config.random_seed}."
    )
    return EventStudyResult(
        config=config,
        events=events,
        reports=reports,
        gate=gate,
        price_source=price_source,
        mode=mode,
        notes=notes,
    )
