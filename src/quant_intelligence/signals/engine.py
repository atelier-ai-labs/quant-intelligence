"""Turn a point-in-time risk-factor diff (+ insider buys) into a validated, fail-closed signal via local Ollama."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from quant_intelligence.edgar.client import FilingRef
from quant_intelligence.edgar.diff import RiskFactorDiff
from quant_intelligence.edgar.form4 import InsiderTransaction

from .ollama import OllamaClient, OllamaError
from .schema import LLMSignalPayload, RiskSignal, validate_citations

log = logging.getLogger(__name__)
PROMPT_VERSION = "risk-diff-v1"
MAX_ADDED, MAX_REMOVED, MAX_CHANGED, MAX_PARAGRAPH_CHARS = 6, 4, 4, 600

SYSTEM_PROMPT = """You are a cautious equity research assistant. You read ONLY the SEC filing excerpts provided and \
classify whether the year-over-year (or consecutive-period) change in a company's Risk Factors section \
(Form 10-K or 10-Q Item 1A) is bullish, bearish, or neutral for the stock.
Rules:
- Use only the provided text. No outside knowledge, no price predictions.
- Every citation must copy a short snippet (one sentence or clause, under 300 characters) EXACTLY, character for character, \
from the provided text, and use the accession number shown in that excerpt's label.
- Bullish or bearish requires at least one citation. If the evidence is mixed, boilerplate, or unclear, answer neutral with low confidence.
- confidence is a number from 0 to 1.
Respond with a single JSON object: {\"direction\": \"bullish\"|\"bearish\"|\"neutral\", \"confidence\": number, \"rationale\": string, \
\"citations\": [{\"accession_no\": string, \"snippet\": string}]}."""


class PointInTimeViolation(ValueError):
    pass


@dataclass(frozen=True)
class SignalInputs:
    ticker: str
    new_filing: FilingRef
    old_filing: FilingRef
    diff: RiskFactorDiff
    insider_buys: tuple[InsiderTransaction, ...] = field(default_factory=tuple)

    @property
    def as_of(self) -> datetime:
        return self.new_filing.accepted_at

    def check_point_in_time(self) -> None:
        """Every datum used must have been publicly accepted by EDGAR at or before as_of."""
        allowed = {"10-K", "10-Q"}
        if self.as_of.tzinfo is None: raise PointInTimeViolation("as_of must be timezone-aware")
        if self.new_filing.form not in allowed or self.old_filing.form not in allowed:
            raise PointInTimeViolation(f"diff inputs must be 10-K/10-Q, got {self.old_filing.form}->{self.new_filing.form}")
        if self.old_filing.accepted_at >= self.as_of:
            raise PointInTimeViolation(f"prior filing {self.old_filing.accession_no} accepted at/after as_of")
        for buy in self.insider_buys:
            if buy.accepted_at > self.as_of:
                raise PointInTimeViolation(
                    f"Form 4 {buy.accession_no} accepted {buy.accepted_at.isoformat()} after as_of {self.as_of.isoformat()}"
                )


def _clip(text: str) -> str:
    return text if len(text) <= MAX_PARAGRAPH_CHARS else text[:MAX_PARAGRAPH_CHARS].rsplit(" ", 1)[0] + " ..."


def build_prompt(inputs: SignalInputs) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Return (chat messages, sources). sources maps accession -> exactly the text shown to the model for it."""
    new_acc, old_acc = inputs.new_filing.accession_no, inputs.old_filing.accession_no
    new_form, old_form = inputs.new_filing.form, inputs.old_filing.form
    new_parts: list[str] = []; old_parts: list[str] = []; sections: list[str] = []
    for paragraph in inputs.diff.added[:MAX_ADDED]:
        clipped = _clip(paragraph); new_parts.append(clipped); sections.append(f"[ADDED in {new_form} {new_acc}]\n{clipped}")
    for change in sorted(inputs.diff.changed, key=lambda c: c.ratio)[:MAX_CHANGED]:
        old_text, new_text = _clip(change.old), _clip(change.new)
        old_parts.append(old_text); new_parts.append(new_text)
        sections.append(
            f"[CHANGED - prior wording, {old_form} {old_acc}]\n{old_text}\n"
            f"[CHANGED - new wording, {new_form} {new_acc}]\n{new_text}"
        )
    for paragraph in inputs.diff.removed[:MAX_REMOVED]:
        clipped = _clip(paragraph); old_parts.append(clipped); sections.append(f"[REMOVED, was in {old_form} {old_acc}]\n{clipped}")
    sources: dict[str, str] = {new_acc: "\n".join(new_parts), old_acc: "\n".join(old_parts)}
    buy_lines = []
    for buy in inputs.insider_buys:
        line = buy.describe(); buy_lines.append(f"[FORM 4 {buy.accession_no}]\n{line}")
        sources[buy.accession_no] = (sources.get(buy.accession_no, "") + "\n" + line).strip()
    stats = inputs.diff.summary()
    user = (
        f"Company ticker: {inputs.ticker}\n"
        f"Current {new_form}: {new_acc} accepted {inputs.as_of.isoformat()}\n"
        f"Prior {old_form}: {old_acc} accepted {inputs.old_filing.accepted_at.isoformat()}\n"
        f"Risk Factors diff stats: {json.dumps(stats)}\n\n=== Risk Factors changes (excerpts) ===\n"
        + "\n\n".join(sections)
        + f"\n\n=== Insider open-market purchases known as of the current {new_form} ===\n"
        + ("\n".join(buy_lines) if buy_lines else "None reported in the lookback window.")
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], sources


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")


def generate_signal(inputs: SignalInputs, client: OllamaClient, replay_log: str | Path, *,
                    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                    prefer_replay: bool = True) -> RiskSignal:
    """Always returns a RiskSignal; any failure yields status=no_signal (neutral, confidence 0) with a reason.

    When prefer_replay is True, reuse a prior JSONL record for the same (ticker, new, old) accession
    pair without calling Ollama (sample expansion path).
    """
    if prefer_replay:
        from quant_intelligence.event_study.replay import find_cached_signal
        cached = find_cached_signal(
            replay_log,
            inputs.ticker,
            inputs.new_filing.accession_no,
            inputs.old_filing.accession_no,
            insider_buy_accessions=[b.accession_no for b in inputs.insider_buys],
            insider_buy_accepted_at=[b.accepted_at.isoformat() for b in inputs.insider_buys],
        )
        if cached is not None:
            log.info(
                "replaying cached signal for %s %s->%s",
                inputs.ticker,
                inputs.old_filing.accession_no,
                inputs.new_filing.accession_no,
            )
            return cached

    record: dict[str, Any] = {"ts": now().isoformat(), "prompt_version": PROMPT_VERSION, "model": client.model, "host": client.host,
                              "ticker": inputs.ticker, "as_of": inputs.as_of.isoformat(), "new_accession": inputs.new_filing.accession_no,
                              "old_accession": inputs.old_filing.accession_no,
                              "new_form": inputs.new_filing.form, "old_form": inputs.old_filing.form,
                              "insider_buy_accessions": [b.accession_no for b in inputs.insider_buys],
                              "insider_buy_accepted_at": [b.accepted_at.isoformat() for b in inputs.insider_buys],
                              "messages": None, "raw_output": None, "parsed": None}

    def finish(signal: RiskSignal) -> RiskSignal:
        record["signal"] = signal.model_dump(mode="json")
        try:
            _append_jsonl(Path(replay_log), record)
        except OSError as exc:  # the replay log is required evidence: without it, fail closed
            log.error("replay log write failed (%s); discarding signal", exc)
            return RiskSignal.no_signal(inputs.ticker, inputs.as_of, client.model, f"replay log write failed: {exc}")
        if signal.status == "no_signal": log.warning("no signal for %s as of %s: %s", inputs.ticker, inputs.as_of.isoformat(), signal.reason)
        return signal

    def fail(reason: str) -> RiskSignal:
        return finish(RiskSignal.no_signal(inputs.ticker, inputs.as_of, client.model, reason))

    try:
        inputs.check_point_in_time()
    except PointInTimeViolation as exc:
        return fail(f"point-in-time violation: {exc}")
    if not (inputs.diff.added or inputs.diff.removed or inputs.diff.changed):
        return fail("empty risk-factor diff; nothing to evaluate")
    messages, sources = build_prompt(inputs)
    record["messages"], record["sources"] = messages, sources
    try:
        raw = client.chat(messages, LLMSignalPayload.model_json_schema())
    except OllamaError as exc:
        return fail(f"ollama unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001 - anything unexpected is fail-closed, never propagated as a signal
        return fail(f"ollama call failed: {type(exc).__name__}: {exc}")
    record["raw_output"] = raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return fail(f"invalid JSON from model: {exc}")
    try:
        payload = LLMSignalPayload.model_validate(data)
    except ValidationError as exc:
        return fail(f"schema validation failed: {exc.error_count()} error(s): {exc.errors()[0]['msg']}")
    record["parsed"] = payload.model_dump(mode="json")
    citation_error = validate_citations(payload, sources)
    if citation_error: return fail(f"citation rejected: {citation_error}")
    return finish(RiskSignal(ticker=inputs.ticker, direction=payload.direction, confidence=payload.confidence, rationale=payload.rationale,
                             citations=tuple(payload.citations), as_of=inputs.as_of, model=client.model, status="ok"))
