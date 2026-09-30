"""Multi-event EDGAR history: consecutive 10-K pairs + 10-Q Item 1A diffs.

Re-exports the single-pair API from event_inputs_core so pipeline and tests can
keep importing from quant_intelligence.signals.event_inputs / pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from quant_intelligence.edgar.client import FilingRef, SecClient, SecFetchError

from .event_inputs_core import (
    ALLOWED_FORMS,
    DEFAULT_CONFIG,
    PipelineError,
    PipelineReport,
    UniverseConfig,
    _pair_event,
    _try_extract_paragraphs,
    build_inputs,
    load_universe,
    select_10k_pair,
    select_form4,
)
from .engine import SignalInputs

log = logging.getLogger(__name__)

# Re-export everything tests/pipeline expect from this module.
__all__ = [
    "ALLOWED_FORMS",
    "DEFAULT_CONFIG",
    "PipelineError",
    "PipelineReport",
    "UniverseConfig",
    "SignalInputs",
    "_prior_extractable",
    "_yoy_prior_10q",
    "build_event_inputs",
    "build_inputs",
    "load_universe",
    "select_10k_pair",
    "select_form4",
]


def _yoy_prior_10q(candidate: FilingRef, earlier: list[FilingRef]) -> FilingRef | None:
    """Prefer a prior 10-Q filed ~1 year earlier (same fiscal quarter)."""
    target = candidate.filing_date.toordinal() - 365
    window = []
    for prior in earlier:
        if prior.form != "10-Q":
            continue
        delta = abs(prior.filing_date.toordinal() - target)
        if 300 <= (candidate.filing_date.toordinal() - prior.filing_date.toordinal()) <= 430:
            window.append((delta, prior))
    if not window:
        return None
    window.sort(key=lambda item: item[0])
    return window[0][1]


def _prior_extractable(
    candidate: FilingRef,
    earlier: list[FilingRef],
    paras_by_acc: dict[str, list[str] | None],
) -> tuple[FilingRef, list[str], str] | None:
    """Pick prior filing for a 10-Q event: YoY 10-Q, else consecutive 10-Q, else prior 10-K."""
    yoy = _yoy_prior_10q(candidate, earlier)
    if yoy is not None and paras_by_acc.get(yoy.accession_no):
        return yoy, paras_by_acc[yoy.accession_no], "10q_yoy"  # type: ignore[return-value]
    for prior in reversed(earlier):  # earlier is ascending; take nearest prior with text
        if prior.accession_no == candidate.accession_no:
            continue
        text = paras_by_acc.get(prior.accession_no)
        if not text:
            continue
        if prior.form == "10-Q":
            return prior, text, "10q_qoq"
        if prior.form == "10-K":
            return prior, text, "10q_vs_10k"
    return None


def build_event_inputs(
    client: SecClient,
    ticker: str,
    *,
    cutoff: datetime | None = None,
    lookback_days: int = 180,
    max_form4: int = 40,
    max_10k_pairs: int = 4,
    include_10q: bool = True,
    max_10q_events: int = 4,
    min_10k_history: int = 6,
    min_10q_history: int = 12,
) -> list[tuple[SignalInputs, PipelineReport]]:
    """All consecutive 10-K pairs (newest first, capped) plus extractable 10-Q Item 1A diffs.

    10-Q pairing (documented): prefer YoY prior 10-Q (~365d), else nearest prior 10-Q with
    extractable Item 1A, else nearest prior 10-K with extractable Item 1A. Missing Item 1A
    fails closed for that event only (ticker continues).
    """
    cutoff = cutoff or datetime.now(timezone.utc)
    if cutoff.tzinfo is None:
        raise PipelineError("cutoff must be timezone-aware")
    ticker = ticker.upper()
    forms: tuple[str, ...] = ("10-K", "10-Q", "4") if include_10q else ("10-K", "4")
    filings = client.list_filings(
        ticker,
        forms,
        min_10k=min_10k_history,
        min_10q=min_10q_history if include_10q else 0,
        max_extra_pages=8,
    )
    events: list[tuple[SignalInputs, PipelineReport]] = []

    tenks = sorted(
        (f for f in filings if f.form == "10-K" and f.accepted_at <= cutoff),
        key=lambda f: f.accepted_at,
    )
    # consecutive annual pairs; keep the newest max_10k_pairs
    tenk_pairs: list[tuple[FilingRef, FilingRef]] = []
    for i in range(1, len(tenks)):
        tenk_pairs.append((tenks[i - 1], tenks[i]))
    tenk_pairs = tenk_pairs[-max_10k_pairs:] if max_10k_pairs > 0 else tenk_pairs
    for old, new in reversed(tenk_pairs):  # newest events first
        try:
            events.append(
                _pair_event(
                    client, ticker, cutoff, new, old, filings,
                    lookback_days=lookback_days, max_form4=max_form4, event_kind="10k_yoy",
                )
            )
        except (PipelineError, SecFetchError) as exc:
            log.warning("10-K pair %s->%s skipped: %s", old.accession_no, new.accession_no, exc)

    if not include_10q or max_10q_events <= 0:
        if not events and len(tenks) < 2:
            raise PipelineError(f"need two 10-K filings accepted on/before {cutoff.isoformat()}, found {len(tenks)}")
        return events

    tenqs = sorted(
        (f for f in filings if f.form == "10-Q" and f.accepted_at <= cutoff),
        key=lambda f: f.accepted_at,
    )
    # Bound document fetches: only recent 10-Qs + the 10-Ks already used for annual pairs.
    recent_q = tenqs[-(max_10q_events * 3 + 4):] if tenqs else []
    tenk_for_prior = tenks[-(max_10k_pairs + 2):] if tenks else []
    paras_by_acc: dict[str, list[str] | None] = {}

    def _paras(filing: FilingRef) -> list[str] | None:
        if filing.accession_no not in paras_by_acc:
            paras_by_acc[filing.accession_no] = _try_extract_paragraphs(client, filing)
        return paras_by_acc[filing.accession_no]

    for filing in tenk_for_prior:
        _paras(filing)

    q_events = 0
    for new in reversed(recent_q):  # newest first
        if q_events >= max_10q_events:
            break
        new_paras = _paras(new)
        if not new_paras:
            continue
        # Lazily extract YoY / consecutive priors only when needed.
        earlier = [f for f in (tenk_for_prior + recent_q) if f.accepted_at < new.accepted_at]
        earlier.sort(key=lambda f: f.accepted_at)
        # Prefetch YoY candidate if present so _prior_extractable can see it.
        yoy = _yoy_prior_10q(new, earlier)
        if yoy is not None:
            _paras(yoy)
        for prior in reversed(earlier):
            if prior.form in {"10-Q", "10-K"} and prior.accession_no not in paras_by_acc:
                _paras(prior)
                break
        prior = _prior_extractable(new, earlier, paras_by_acc)
        if prior is None:
            continue
        old, old_paras, kind = prior
        try:
            events.append(
                _pair_event(
                    client, ticker, cutoff, new, old, filings,
                    lookback_days=lookback_days, max_form4=max_form4, event_kind=kind,
                    new_paras=new_paras, old_paras=old_paras,
                )
            )
            q_events += 1
        except (PipelineError, SecFetchError) as exc:
            log.warning("10-Q event %s skipped: %s", new.accession_no, exc)

    if not events:
        raise PipelineError(
            f"no usable 10-K/10-Q Item 1A pairs on/before {cutoff.isoformat()} for {ticker}"
        )
    return events
