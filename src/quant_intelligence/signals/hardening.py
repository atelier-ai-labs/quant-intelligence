"""Signal hardening for prompt version risk-diff-v2.

Portfolio goal: honest AI evals + safety. These rules are enforced in code (not
only in the prompt) so a noisy local model cannot bypass them.

Rules (documented here, in engine.SYSTEM_PROMPT, README, and docs/EVENT_STUDY.md):

1) Citations must quote real year-over-year Item 1A *changes*:
   - Snippet must appear in the added / removed / changed paragraph sets of the
     RiskFactorDiff used for that as_of (new accession → added + changed.new;
     old accession → removed + changed.old).
   - Snippet must appear in the text actually shown to the model for that accession.
   - Snippet must NOT appear in any unchanged paragraph (boilerplate present in
     both years). Unchanged boilerplate is rejected.
   - Citations may only reference the two Item 1A filing accessions (not Form 4).
   - Bad citations → fail closed (no_signal).

2) Bullish only if clustered Form 4 insider buys confirm:
   - ≥2 open-market purchases (code P, acquired A) by *different* insiders
     with accepted_at in [as_of − INSIDER_CLUSTER_WINDOW_DAYS, as_of].
   - Otherwise bullish is capped to neutral (confidence ≤ 0.4).
   - Bearish from risk-text worsening is allowed with a higher bar:
     confidence ≥ BEARISH_MIN_CONFIDENCE, or ≥1 citation grounded in an ADDED
     paragraph; else capped to neutral (confidence ≤ 0.4).

3) Thin evidence → low confidence / neutral:
   - If (added+removed+changed) < THIN_MIN_DELTA_PARAS, or diff.similarity ≥
     THIN_SIMILARITY_CEILING → force neutral with confidence ≤ THIN_MAX_CONFIDENCE.

4) Fail-closed on invalid JSON / schema / Ollama down / replay-log write failure
   remains the engine's responsibility (unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from quant_intelligence.edgar.diff import RiskFactorDiff
from quant_intelligence.edgar.form4 import InsiderTransaction

from .schema import LLMSignalPayload, normalize

# --- documented constants --------------------------------------------------------------------

INSIDER_CLUSTER_WINDOW_DAYS = 90
INSIDER_CLUSTER_MIN_DISTINCT = 2
BEARISH_MIN_CONFIDENCE = 0.7
CAP_NEUTRAL_CONFIDENCE = 0.4
THIN_MIN_DELTA_PARAS = 2
THIN_SIMILARITY_CEILING = 0.97
THIN_MAX_CONFIDENCE = 0.35


@dataclass(frozen=True)
class HardeningDecision:
    """Post-LLM calibration result. fail_reason set ⇒ engine must emit no_signal."""

    direction: str
    confidence: float
    rationale: str
    fail_reason: str | None = None
    notes: tuple[str, ...] = ()


def delta_corpus(diff: RiskFactorDiff, new_accession: str, old_accession: str) -> dict[str, str]:
    """Map filing accession → concatenated added/removed/changed paragraph text."""
    new_parts = list(diff.added) + [c.new for c in diff.changed]
    old_parts = list(diff.removed) + [c.old for c in diff.changed]
    return {
        new_accession: "\n".join(new_parts),
        old_accession: "\n".join(old_parts),
    }


def snippet_in_unchanged(snippet: str, unchanged: tuple[str, ...]) -> bool:
    """True when the (normalized) snippet appears inside any unchanged paragraph."""
    needle = normalize(snippet)
    if not needle:
        return False
    for para in unchanged:
        if needle in normalize(para):
            return True
    return False


def validate_citations_against_diff(
    payload: LLMSignalPayload,
    *,
    new_accession: str,
    old_accession: str,
    diff: RiskFactorDiff,
    prompt_sources: dict[str, str],
) -> str | None:
    """Return None if citations are grounded in real YoY changes; else a rejection reason.

    Rejects: unknown accession, Form 4 accession, snippet missing from prompt text,
    snippet missing from the delta corpus, or snippet that also appears in unchanged
    boilerplate paragraphs.
    """
    allowed = {new_accession, old_accession}
    corpus = delta_corpus(diff, new_accession, old_accession)
    norm_prompt = {acc: normalize(text) for acc, text in prompt_sources.items()}
    norm_corpus = {acc: normalize(text) for acc, text in corpus.items()}
    unchanged = tuple(diff.unchanged)

    for i, citation in enumerate(payload.citations):
        acc = citation.accession_no
        if acc not in allowed:
            return (
                f"citation {i} references non-Item-1A accession {acc} "
                f"(only {new_accession} / {old_accession} allowed)"
            )
        snippet = normalize(citation.snippet)
        if not snippet:
            return f"citation {i} snippet empty after normalize"
        shown = norm_prompt.get(acc, "")
        if snippet not in shown:
            return f"citation {i} snippet not found in prompt text for {acc}"
        if snippet not in norm_corpus.get(acc, ""):
            return f"citation {i} snippet not found in added/removed/changed diff text for {acc}"
        if snippet_in_unchanged(citation.snippet, unchanged):
            return (
                f"citation {i} snippet is unchanged boilerplate "
                f"(appears in both years' Item 1A); not a real YoY change"
            )
    return None


def citation_grounds_added(payload: LLMSignalPayload, diff: RiskFactorDiff, new_accession: str) -> bool:
    """True if at least one citation snippet appears in an ADDED paragraph of the new filing."""
    added_blob = normalize("\n".join(diff.added))
    if not added_blob:
        return False
    for citation in payload.citations:
        if citation.accession_no != new_accession:
            continue
        if normalize(citation.snippet) in added_blob:
            return True
    return False


def has_insider_buy_cluster(
    buys: tuple[InsiderTransaction, ...] | list[InsiderTransaction],
    as_of: datetime,
    *,
    window_days: int = INSIDER_CLUSTER_WINDOW_DAYS,
    min_distinct: int = INSIDER_CLUSTER_MIN_DISTINCT,
) -> bool:
    """≥ min_distinct distinct filers with open-market P buys in the window ending at as_of."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    start = as_of - timedelta(days=window_days)
    filers: set[str] = set()
    for buy in buys:
        if not buy.is_open_market_purchase:
            continue
        if buy.accepted_at.tzinfo is None:
            continue
        if start <= buy.accepted_at <= as_of:
            name = (buy.filer or "").strip().lower()
            if name:
                filers.add(name)
    return len(filers) >= min_distinct


def is_thin_evidence(diff: RiskFactorDiff) -> bool:
    delta = len(diff.added) + len(diff.removed) + len(diff.changed)
    return delta < THIN_MIN_DELTA_PARAS or diff.similarity >= THIN_SIMILARITY_CEILING


def apply_hardening(
    payload: LLMSignalPayload,
    *,
    diff: RiskFactorDiff,
    new_accession: str,
    old_accession: str,
    prompt_sources: dict[str, str],
    insider_buys: tuple[InsiderTransaction, ...] | list[InsiderTransaction],
    as_of: datetime,
) -> HardeningDecision:
    """Validate citations then calibrate direction/confidence. Fail closed on bad citations."""
    citation_error = validate_citations_against_diff(
        payload,
        new_accession=new_accession,
        old_accession=old_accession,
        diff=diff,
        prompt_sources=prompt_sources,
    )
    if citation_error:
        return HardeningDecision(
            direction="neutral",
            confidence=0.0,
            rationale="",
            fail_reason=f"citation rejected: {citation_error}",
        )

    direction = payload.direction
    confidence = float(payload.confidence)
    rationale = payload.rationale
    notes: list[str] = []

    if is_thin_evidence(diff):
        notes.append("thin_evidence")
        direction = "neutral"
        confidence = min(confidence, THIN_MAX_CONFIDENCE)
        rationale = f"[thin evidence] {rationale}"

    if direction == "bullish":
        if not has_insider_buy_cluster(insider_buys, as_of):
            notes.append("bullish_blocked_no_insider_cluster")
            direction = "neutral"
            confidence = min(confidence, CAP_NEUTRAL_CONFIDENCE)
            rationale = (
                f"[bullish capped: need ≥{INSIDER_CLUSTER_MIN_DISTINCT} distinct-insider "
                f"Form 4 code-P buys within {INSIDER_CLUSTER_WINDOW_DAYS}d] {rationale}"
            )
    elif direction == "bearish":
        strong = confidence >= BEARISH_MIN_CONFIDENCE or citation_grounds_added(
            payload, diff, new_accession
        )
        if not strong:
            notes.append("bearish_below_higher_bar")
            direction = "neutral"
            confidence = min(confidence, CAP_NEUTRAL_CONFIDENCE)
            rationale = (
                f"[bearish capped: need conf≥{BEARISH_MIN_CONFIDENCE} or an ADDED-paragraph "
                f"citation] {rationale}"
            )

    return HardeningDecision(
        direction=direction,
        confidence=confidence,
        rationale=rationale,
        fail_reason=None,
        notes=tuple(notes),
    )
