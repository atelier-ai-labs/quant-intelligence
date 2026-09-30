"""Year-over-year paragraph diff of Item 1A risk factors."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

DEFAULT_CHANGE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ChangedParagraph:
    old: str
    new: str
    ratio: float


@dataclass(frozen=True)
class RiskFactorDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[ChangedParagraph, ...]
    unchanged_count: int
    similarity: float
    old_paragraph_count: int = 0
    new_paragraph_count: int = 0
    # Paragraphs present verbatim in both years (boilerplate). Used by risk-diff-v2
    # citation hardening to reject snippets that also appear in unchanged text.
    unchanged: tuple[str, ...] = ()

    def summary(self) -> dict[str, float | int]:
        return {"old_paragraphs": self.old_paragraph_count, "new_paragraphs": self.new_paragraph_count, "added": len(self.added),
                "removed": len(self.removed), "changed": len(self.changed), "unchanged": self.unchanged_count, "similarity": round(self.similarity, 4)}


def _ratio(a: str, b: str) -> float:
    """Word-level difflib ratio, with a cheap vocabulary-overlap prefilter (keeps 10-K diffs fast)."""
    wa, wb = a.lower().split(), b.lower().split()
    sa, sb = set(wa), set(wb)
    if not sa or not sb or len(sa & sb) / len(sa | sb) < 0.3: return 0.0
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def diff_paragraphs(old: list[str], new: list[str], threshold: float = DEFAULT_CHANGE_THRESHOLD) -> RiskFactorDiff:
    """Classify paragraphs as unchanged (exact), changed (word-level difflib ratio >= threshold), added, or removed.

    similarity = sum(ratio * (len(old)+len(new))) over matched pairs / total characters on both sides, in [0, 1].
    """
    if not 0 < threshold <= 1: raise ValueError("threshold must be in (0, 1]")
    unmatched_old: list[str] = []
    unmatched_new: list[str] = []
    unchanged_paras: list[str] = []
    weighted = 0.0
    unchanged = 0
    remaining = Counter(old)
    for paragraph in new:
        if remaining[paragraph] > 0:
            remaining[paragraph] -= 1; unchanged += 1; weighted += 2 * len(paragraph)
            unchanged_paras.append(paragraph)
        else:
            unmatched_new.append(paragraph)
    for paragraph in old:  # leftover old paragraphs, in document order
        if remaining[paragraph] > 0:
            remaining[paragraph] -= 1; unmatched_old.append(paragraph)

    candidates = sorted(((_ratio(o, n), oi, ni) for ni, n in enumerate(unmatched_new) for oi, o in enumerate(unmatched_old)), reverse=True)
    used_old: set[int] = set(); used_new: set[int] = set(); changed: list[tuple[int, ChangedParagraph]] = []
    for ratio, oi, ni in candidates:
        if ratio < threshold: break
        if oi in used_old or ni in used_new: continue
        used_old.add(oi); used_new.add(ni)
        o, n = unmatched_old[oi], unmatched_new[ni]
        changed.append((ni, ChangedParagraph(o, n, round(ratio, 4))))
        weighted += ratio * (len(o) + len(n))
    total = sum(map(len, old)) + sum(map(len, new))
    similarity = 1.0 if total == 0 else min(1.0, weighted / total)
    return RiskFactorDiff(
        added=tuple(n for i, n in enumerate(unmatched_new) if i not in used_new),
        removed=tuple(o for i, o in enumerate(unmatched_old) if i not in used_old),
        changed=tuple(c for _, c in sorted(changed, key=lambda x: x[0])),
        unchanged_count=unchanged, similarity=similarity, old_paragraph_count=len(old), new_paragraph_count=len(new),
        unchanged=tuple(unchanged_paras))
