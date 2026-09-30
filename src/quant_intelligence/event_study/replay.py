"""Load validated RiskSignals from the PR #1 JSONL replay log without calling Ollama."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from quant_intelligence.signals.schema import RiskSignal

DEFAULT_REPLAY_LOG = Path("data/signals/replay.jsonl")


def load_replay_signals(path: str | Path = DEFAULT_REPLAY_LOG) -> list[RiskSignal]:
    """Parse every line that contains a `signal` object into a RiskSignal.

    Lines without a signal payload are skipped. Order is file order (append order).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"replay log not found: {path}")
    signals: list[RiskSignal] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        payload = record.get("signal")
        if payload is None:
            continue
        # Prefer the nested signal fields; fall back to record-level ticker/as_of for older rows.
        if "ticker" not in payload and "ticker" in record:
            payload = {**payload, "ticker": record["ticker"]}
        if "as_of" not in payload and "as_of" in record:
            payload = {**payload, "as_of": record["as_of"]}
        signals.append(RiskSignal.model_validate(payload))
    return signals


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
