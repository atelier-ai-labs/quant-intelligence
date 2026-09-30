"""Expanded multi-event EDGAR input assembly (imported by pipeline).

Supports a single latest 10-K pair (`build_inputs`) and an expanded multi-event
history (`build_event_inputs`) that walks consecutive annual 10-K pairs plus
10-Q Item 1A YoY / consecutive-period diffs when extractable.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant_intelligence.edgar.client import FilingRef, SecClient, SecFetchError
from quant_intelligence.edgar.diff import diff_paragraphs
from quant_intelligence.edgar.form4 import Form4ParseError, open_market_purchases, parse_form4
from quant_intelligence.edgar.risk_factors import RiskFactorExtractionError, extract_item_1a, split_paragraphs

from .engine import SignalInputs

log = logging.getLogger(__name__)
DEFAULT_CONFIG = Path("config/edgar_universe.toml")
ALLOWED_FORMS = frozenset({"10-K", "10-Q"})


class PipelineError(RuntimeError):
    """Inputs cannot be assembled confidently; no signal may be produced."""


@dataclass(frozen=True)
class UniverseConfig:
    tickers: tuple[str, ...]
    form4_lookback_days: int = 180
    max_form4_filings: int = 40
    requests_per_second: float = 5.0
    min_confidence: float = 0.6
    order_quantity: int = 1
    max_10k_pairs: int = 4
    include_10q: bool = True
    max_10q_events: int = 4
    min_10k_history: int = 6
    min_10q_history: int = 12


def load_universe(path: str | Path = DEFAULT_CONFIG) -> UniverseConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    edgar, signals = data.get("edgar", {}), data.get("signals", {})
    tickers = tuple(str(t).upper() for t in edgar.get("tickers", []))
    if not tickers:
        raise PipelineError(f"no tickers configured in {path}")
    return UniverseConfig(
        tickers,
        int(edgar.get("form4_lookback_days", 180)),
        int(edgar.get("max_form4_filings", 40)),
        float(edgar.get("requests_per_second", 5.0)),
        float(signals.get("min_confidence", 0.6)),
        int(signals.get("order_quantity", 1)),
        int(edgar.get("max_10k_pairs", 4)),
        bool(edgar.get("include_10q", True)),
        int(edgar.get("max_10q_events", 4)),
        int(edgar.get("min_10k_history", 6)),
        int(edgar.get("min_10q_history", 12)),
    )


@dataclass
class PipelineReport:
    ticker: str
    cutoff: str
    event_kind: str = "10k_yoy"
    filings: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    form4: dict[str, Any] = field(default_factory=dict)


def select_10k_pair(filings: list[FilingRef], cutoff: datetime) -> tuple[FilingRef, FilingRef]:
    tenks = sorted(
        (f for f in filings if f.form == "10-K" and f.accepted_at <= cutoff),
        key=lambda f: f.accepted_at,
        reverse=True,
    )
    if len(tenks) < 2:
        raise PipelineError(f"need two 10-K filings accepted on/before {cutoff.isoformat()}, found {len(tenks)}")
    return tenks[0], tenks[1]


def select_form4(filings: list[FilingRef], as_of: datetime, lookback_days: int, limit: int) -> list[FilingRef]:
    start = as_of - timedelta(days=lookback_days)
    chosen = [f for f in filings if f.form == "4" and start <= f.accepted_at <= as_of]
    return sorted(chosen, key=lambda f: f.accepted_at, reverse=True)[:limit]


def _extract_paragraphs(client: SecClient, filing: FilingRef) -> list[str]:
    try:
        return split_paragraphs(extract_item_1a(client.fetch_document(filing)))
    except RiskFactorExtractionError as exc:
        raise PipelineError(f"Item 1A extraction failed for {filing.accession_no}: {exc}") from exc


def _try_extract_paragraphs(client: SecClient, filing: FilingRef) -> list[str] | None:
    try:
        return _extract_paragraphs(client, filing)
    except (PipelineError, SecFetchError) as exc:
        log.info("skip %s %s: %s", filing.form, filing.accession_no, exc)
        return None


def _form4_bundle(
    client: SecClient,
    filings: list[FilingRef],
    as_of: datetime,
    ticker: str,
    lookback_days: int,
    max_form4: int,
) -> tuple[list, dict[str, Any]]:
    buys, parsed, skipped = [], 0, []
    for filing in select_form4(filings, as_of, lookback_days, max_form4):
        try:
            transactions = parse_form4(client.fetch_document(filing), filing.accession_no, filing.accepted_at)
        except (SecFetchError, Form4ParseError) as exc:
            skipped.append({"accession_no": filing.accession_no, "reason": str(exc)})
            continue
        parsed += 1
        buys.extend(t for t in open_market_purchases(transactions) if t.issuer_ticker in {"", ticker})
    report = {
        "window_start": (as_of - timedelta(days=lookback_days)).isoformat(),
        "window_end": as_of.isoformat(),
        "filings_parsed": parsed,
        "filings_skipped": skipped,
        "open_market_purchases": [b.to_json() for b in buys],
    }
    return buys, report


def _pair_event(
    client: SecClient,
    ticker: str,
    cutoff: datetime,
    new: FilingRef,
    old: FilingRef,
    filings: list[FilingRef],
    *,
    lookback_days: int,
    max_form4: int,
    event_kind: str,
    new_paras: list[str] | None = None,
    old_paras: list[str] | None = None,
) -> tuple[SignalInputs, PipelineReport]:
    if new.form not in ALLOWED_FORMS or old.form not in ALLOWED_FORMS:
        raise PipelineError(f"unsupported form pair {old.form}->{new.form}")
    if old.accepted_at >= new.accepted_at:
        raise PipelineError(f"prior filing {old.accession_no} not strictly before {new.accession_no}")
    report = PipelineReport(ticker, cutoff.isoformat(), event_kind=event_kind)
    report.filings = {"new": new.to_json(), "old": old.to_json(), "new_10k": new.to_json(), "old_10k": old.to_json()}
    paragraphs_old = old_paras if old_paras is not None else _extract_paragraphs(client, old)
    paragraphs_new = new_paras if new_paras is not None else _extract_paragraphs(client, new)
    diff = diff_paragraphs(paragraphs_old, paragraphs_new)
    report.diff = diff.summary()
    buys, form4_report = _form4_bundle(client, filings, new.accepted_at, ticker, lookback_days, max_form4)
    report.form4 = form4_report
    return SignalInputs(ticker, new, old, diff, tuple(buys)), report


def build_inputs(
    client: SecClient,
    ticker: str,
    *,
    cutoff: datetime | None = None,
    lookback_days: int = 180,
    max_form4: int = 40,
) -> tuple[SignalInputs, PipelineReport]:
    """Latest 10-K pair only (backward compatible with PR #1/#2 callers)."""
    cutoff = cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        raise PipelineError("cutoff must be timezone-aware")
    ticker = ticker.upper()
    filings = client.list_filings(ticker, ("10-K", "4"))
    new, old = select_10k_pair(filings, cutoff)
    return _pair_event(
        client, ticker, cutoff, new, old, filings,
        lookback_days=lookback_days, max_form4=max_form4, event_kind="10k_yoy",
    )
