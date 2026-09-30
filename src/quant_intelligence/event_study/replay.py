"""Load validated RiskSignals from the PR #1 JSONL replay log without calling Ollama."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from quant_intelligence.signals.schema import RiskSignal

DEFAULT_REPLAY_LOG = Path("data/signals/replay.jsonl")


def load_replay_signals(
    path: str | Path = DEFAULT_REPLAY_LOG,
    *,
    prompt_version: str | None = None,
) -> list[RiskSignal]:
    """Parse every line that contains a `signal` object into a RiskSignal.

    Lines without a signal payload are skipped. Order is file order (append order).
    When `new_accession` is present, duplicate (ticker, new_accession) pairs keep the last row.
    Rows without `new_accession` are never collapsed (fixture / legacy lines).
    When prompt_version is set, only rows with that prompt_version are considered.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"replay log not found: {path}")
    by_key: dict[str, RiskSignal] = {}
    order: list[str] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if prompt_version is not None and record.get("prompt_version") != prompt_version:
            continue
        payload = record.get("signal")
        if payload is None:
            continue
        if "ticker" not in payload and "ticker" in record:
            payload = {**payload, "ticker": record["ticker"]}
        if "as_of" not in payload and "as_of" in record:
            payload = {**payload, "as_of": record["as_of"]}
        signal = RiskSignal.model_validate(payload)
        new_acc = str(record.get("new_accession") or "").strip()
        if new_acc:
            key = f"acc:{signal.ticker.upper()}:{new_acc}"
        else:
            key = f"line:{line_no}"
        if key not in by_key:
            order.append(key)
        by_key[key] = signal
    return [by_key[key] for key in order]


def _form4_fingerprint(
    accessions: Iterable[str] | None,
    accepted_at: Iterable[str] | None = None,
) -> str:
    acc = [str(a) for a in (accessions or []) if a]
    times = [str(t) for t in (accepted_at or [])]
    # Pad times to accessions length for legacy rows that lack accepted_at.
    while len(times) < len(acc):
        times.append("")
    pairs = sorted(zip(acc, times[: len(acc)]))
    return ";".join(f"{a}@{t}" for a, t in pairs)


def find_cached_signal(
    path: str | Path,
    ticker: str,
    new_accession: str,
    old_accession: str,
    *,
    insider_buy_accessions: Iterable[str] | None = None,
    insider_buy_accepted_at: Iterable[str] | None = None,
    prompt_version: str | None = None,
) -> RiskSignal | None:
    """Return the latest replayed RiskSignal for an exact accession pair (+ Form 4 set), or None.

    Used by generate_signal(prefer_replay=True) so expansion only calls Ollama for NEW filings.
    Form 4 accession + acceptance timestamps are part of the key (shown to the model / PIT).
    When prompt_version is set (e.g. risk-diff-v2), only rows with that exact prompt_version match
    — v1 caches must not be reused after a prompt redesign.
    """
    path = Path(path)
    if not path.exists():
        return None
    found: RiskSignal | None = None
    ticker_u = ticker.upper()
    wanted_f4 = _form4_fingerprint(insider_buy_accessions, insider_buy_accepted_at)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(record.get("ticker", "")).upper() != ticker_u:
            continue
        if record.get("new_accession") != new_accession or record.get("old_accession") != old_accession:
            continue
        if prompt_version is not None and record.get("prompt_version") != prompt_version:
            continue
        # Legacy rows (pre-expand) lack insider_buy_accepted_at - match on accessions only.
        if "insider_buy_accepted_at" not in record:
            record_f4 = _form4_fingerprint(record.get("insider_buy_accessions") or [], None)
            wanted_acc_only = _form4_fingerprint(insider_buy_accessions, None)
            if record_f4 != wanted_acc_only:
                continue
        else:
            record_f4 = _form4_fingerprint(
                record.get("insider_buy_accessions") or [],
                record.get("insider_buy_accepted_at") or [],
            )
            if record_f4 != wanted_f4:
                continue
        payload = record.get("signal")
        if payload is None:
            continue
        if "ticker" not in payload:
            payload = {**payload, "ticker": record.get("ticker", ticker_u)}
        if "as_of" not in payload and "as_of" in record:
            payload = {**payload, "as_of": record["as_of"]}
        try:
            found = RiskSignal.model_validate(payload)
        except Exception:  # noqa: BLE001
            continue
    return found


def signals_to_events(signals: list[RiskSignal]) -> list[dict]:
    """Compact event rows used by the study (ticker, as_of, direction, confidence, status)."""
    rows = []
    for signal in signals:
        rows.append(
            {
                "ticker": signal.ticker,
                "as_of": signal.as_of if isinstance(signal.as_of, datetime) else signal.as_of,
                "direction": signal.direction,
                "confidence": signal.confidence,
                "status": signal.status,
                "reason": signal.reason,
                "model": signal.model,
            }
        )
    return rows
