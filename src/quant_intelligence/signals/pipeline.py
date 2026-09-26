"""Point-in-time assembly of signal inputs for one ticker from EDGAR."""

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


def load_universe(path: str | Path = DEFAULT_CONFIG) -> UniverseConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    edgar, signals = data.get("edgar", {}), data.get("signals", {})
    tickers = tuple(str(t).upper() for t in edgar.get("tickers", []))
    if not tickers: raise PipelineError(f"no tickers configured in {path}")
    return UniverseConfig(tickers, int(edgar.get("form4_lookback_days", 180)), int(edgar.get("max_form4_filings", 40)),
                          float(edgar.get("requests_per_second", 5.0)), float(signals.get("min_confidence", 0.6)), int(signals.get("order_quantity", 1)))


@dataclass
class PipelineReport:
    ticker: str
    cutoff: str
    filings: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    form4: dict[str, Any] = field(default_factory=dict)


def select_10k_pair(filings: list[FilingRef], cutoff: datetime) -> tuple[FilingRef, FilingRef]:
    tenks = sorted((f for f in filings if f.form == "10-K" and f.accepted_at <= cutoff), key=lambda f: f.accepted_at, reverse=True)
    if len(tenks) < 2: raise PipelineError(f"need two 10-K filings accepted on/before {cutoff.isoformat()}, found {len(tenks)}")
    return tenks[0], tenks[1]


def select_form4(filings: list[FilingRef], as_of: datetime, lookback_days: int, limit: int) -> list[FilingRef]:
    start = as_of - timedelta(days=lookback_days)
    chosen = [f for f in filings if f.form == "4" and start <= f.accepted_at <= as_of]
    return sorted(chosen, key=lambda f: f.accepted_at, reverse=True)[:limit]


def build_inputs(client: SecClient, ticker: str, *, cutoff: datetime | None = None, lookback_days: int = 180, max_form4: int = 40) -> tuple[SignalInputs, PipelineReport]:
    cutoff = cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None: raise PipelineError("cutoff must be timezone-aware")
    ticker = ticker.upper()
    report = PipelineReport(ticker, cutoff.isoformat())
    filings = client.list_filings(ticker, ("10-K", "4"))
    new, old = select_10k_pair(filings, cutoff)
    report.filings = {"new_10k": new.to_json(), "old_10k": old.to_json()}
    paragraphs = []
    for filing in (old, new):
        try:
            paragraphs.append(split_paragraphs(extract_item_1a(client.fetch_document(filing))))
        except RiskFactorExtractionError as exc:
            raise PipelineError(f"Item 1A extraction failed for {filing.accession_no}: {exc}") from exc
    diff = diff_paragraphs(paragraphs[0], paragraphs[1])
    report.diff = diff.summary()
    buys, parsed, skipped = [], 0, []
    for filing in select_form4(filings, new.accepted_at, lookback_days, max_form4):
        try:
            transactions = parse_form4(client.fetch_document(filing), filing.accession_no, filing.accepted_at)
        except (SecFetchError, Form4ParseError) as exc:
            skipped.append({"accession_no": filing.accession_no, "reason": str(exc)}); continue
        parsed += 1
        buys.extend(t for t in open_market_purchases(transactions) if t.issuer_ticker in {"", ticker})
    report.form4 = {"window_start": (new.accepted_at - timedelta(days=lookback_days)).isoformat(), "window_end": new.accepted_at.isoformat(),
                    "filings_parsed": parsed, "filings_skipped": skipped, "open_market_purchases": [b.to_json() for b in buys]}
    return SignalInputs(ticker, new, old, diff, tuple(buys)), report
