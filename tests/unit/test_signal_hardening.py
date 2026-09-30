"""Hardening rules for risk-diff-v2: citation-vs-diff, insider cluster, thin evidence, fail-closed."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from quant_intelligence.edgar import diff_paragraphs, extract_item_1a, split_paragraphs
from quant_intelligence.edgar.client import FilingRef
from quant_intelligence.edgar.diff import RiskFactorDiff
from quant_intelligence.edgar.form4 import InsiderTransaction
from quant_intelligence.event_study.replay import find_cached_signal
from quant_intelligence.signals.engine import PROMPT_VERSION, SignalInputs, build_prompt, generate_signal
from quant_intelligence.signals.hardening import (
    CAP_NEUTRAL_CONFIDENCE,
    INSIDER_CLUSTER_WINDOW_DAYS,
    THIN_MAX_CONFIDENCE,
    has_insider_buy_cluster,
    validate_citations_against_diff,
)
from quant_intelligence.signals.ollama import OllamaClient, OllamaError
from quant_intelligence.signals.schema import LLMSignalPayload, validate_citations

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
NEW_ACC, OLD_ACC, F4_ACC = "0000999999-25-000101", "0000999999-24-000090", "0001111111-25-000031"
AS_OF = datetime(2025, 12, 1, 21, 30, tzinfo=timezone.utc)
NEW = FilingRef("ACME", 999999, "10-K", NEW_ACC, AS_OF, date(2025, 12, 1), "acme-20251130.htm")
OLD = FilingRef(
    "ACME",
    999999,
    "10-K",
    OLD_ACC,
    datetime(2024, 12, 2, 21, 30, tzinfo=timezone.utc),
    date(2024, 12, 2),
    "acme-20241130.htm",
)
DOJ = "We are the subject of a pending Department of Justice investigation relating to export controls"


def _diff():
    old = split_paragraphs(extract_item_1a((FIXTURES / "tenk_2024.htm").read_bytes()))
    new = split_paragraphs(extract_item_1a((FIXTURES / "tenk_2025.htm").read_bytes()))
    return diff_paragraphs(old, new)


def _buy(filer: str, accepted: datetime, accession: str = F4_ACC) -> InsiderTransaction:
    return InsiderTransaction(
        "ACME", filer, "Officer", date(2025, 9, 15), "P", "A", 1000.0, 10.0, accession, accepted
    )


def make_inputs(*, buys=None) -> SignalInputs:
    if buys is None:
        buys = (_buy("Doe Jane", datetime(2025, 9, 17, 20, 0, tzinfo=timezone.utc)),)
    return SignalInputs("ACME", NEW, OLD, _diff(), tuple(buys))


class FakeTransport:
    def __init__(self, content=None, exc=None):
        self.content, self.exc, self.calls = content, exc, []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.exc:
            raise self.exc
        return {"model": payload["model"], "message": {"role": "assistant", "content": self.content}, "done": True}


def client(transport) -> OllamaClient:
    return OllamaClient("http://127.0.0.1:11434", "llama3.2", 5, transport=transport)


def payload(**overrides) -> str:
    data = {
        "direction": "bearish",
        "confidence": 0.8,
        "rationale": "A new DOJ investigation risk factor was added.",
        "citations": [{"accession_no": NEW_ACC, "snippet": DOJ}],
    }
    data.update(overrides)
    return json.dumps(data)


def test_citation_from_unchanged_boilerplate_rejected():
    """Reject snippets that appear in both a delta paragraph and an unchanged paragraph."""
    shared = "shares of our common stock could decline, possibly significantly or permanently"
    diff = RiskFactorDiff(
        added=(f"{shared}, if one or more of these risks and uncertainties occurs. New cyber risk.",),
        removed=("Old manufacturer concentration risk text that was removed this year.",),
        changed=(),
        unchanged_count=1,
        similarity=0.5,
        old_paragraph_count=2,
        new_paragraph_count=2,
        unchanged=(f"Investing is risky. {shared}, if risks occur. See Item 1A.",),
    )
    pl = LLMSignalPayload.model_validate(
        {
            "direction": "bearish",
            "confidence": 0.8,
            "rationale": "boilerplate cite",
            "citations": [{"accession_no": NEW_ACC, "snippet": shared}],
        }
    )
    sources = {NEW_ACC: diff.added[0], OLD_ACC: diff.removed[0]}
    err = validate_citations_against_diff(
        pl, new_accession=NEW_ACC, old_accession=OLD_ACC, diff=diff, prompt_sources=sources
    )
    assert err is not None and "boilerplate" in err

    pure = LLMSignalPayload.model_validate(
        {
            "direction": "bearish",
            "confidence": 0.8,
            "rationale": "unchanged only",
            "citations": [{"accession_no": NEW_ACC, "snippet": "Investing is risky. See Item 1A."}],
        }
    )
    tainted = {
        NEW_ACC: sources[NEW_ACC] + "\nInvesting is risky. See Item 1A.",
        OLD_ACC: sources[OLD_ACC],
    }
    err2 = validate_citations_against_diff(
        pure, new_accession=NEW_ACC, old_accession=OLD_ACC, diff=diff, prompt_sources=tainted
    )
    assert err2 is not None


def test_citation_from_added_paragraph_accepted():
    diff = _diff()
    pl = LLMSignalPayload.model_validate_json(payload())
    _, sources = build_prompt(make_inputs())
    assert (
        validate_citations_against_diff(
            pl, new_accession=NEW_ACC, old_accession=OLD_ACC, diff=diff, prompt_sources=sources
        )
        is None
    )
    assert validate_citations(pl, sources) is None


def test_citation_from_removed_or_changed_accepted():
    diff = _diff()
    removed_snip = diff.removed[0][:60]
    changed_snip = diff.changed[0].new[:60]
    _, sources = build_prompt(make_inputs())
    for acc, snip in ((OLD_ACC, removed_snip), (NEW_ACC, changed_snip)):
        pl = LLMSignalPayload.model_validate(
            {
                "direction": "bearish",
                "confidence": 0.85,
                "rationale": "delta cite",
                "citations": [{"accession_no": acc, "snippet": snip}],
            }
        )
        assert (
            validate_citations_against_diff(
                pl, new_accession=NEW_ACC, old_accession=OLD_ACC, diff=diff, prompt_sources=sources
            )
            is None
        )


def test_form4_accession_citation_rejected():
    pl = LLMSignalPayload.model_validate(
        {
            "direction": "bullish",
            "confidence": 0.8,
            "rationale": "insider buy",
            "citations": [
                {"accession_no": F4_ACC, "snippet": "open-market purchase of 1000 shares at $10.00"}
            ],
        }
    )
    sources = {NEW_ACC: DOJ, OLD_ACC: "x", F4_ACC: "open-market purchase of 1000 shares at $10.00"}
    err = validate_citations_against_diff(
        pl, new_accession=NEW_ACC, old_accession=OLD_ACC, diff=_diff(), prompt_sources=sources
    )
    assert err is not None and "non-Item-1A" in err


def test_insider_cluster_requires_two_distinct_filers_in_window():
    as_of = AS_OF
    one = (_buy("Alice", as_of - timedelta(days=10)),)
    assert has_insider_buy_cluster(one, as_of) is False
    two_same = (
        _buy("Alice", as_of - timedelta(days=10), "0001111111-25-000031"),
        _buy("Alice", as_of - timedelta(days=20), "0001111111-25-000032"),
    )
    assert has_insider_buy_cluster(two_same, as_of) is False
    two_distinct = (
        _buy("Alice", as_of - timedelta(days=10), "0001111111-25-000031"),
        _buy("Bob", as_of - timedelta(days=20), "0001111111-25-000032"),
    )
    assert has_insider_buy_cluster(two_distinct, as_of) is True
    outside = (
        _buy("Alice", as_of - timedelta(days=INSIDER_CLUSTER_WINDOW_DAYS + 1), "0001111111-25-000031"),
        _buy("Bob", as_of - timedelta(days=INSIDER_CLUSTER_WINDOW_DAYS + 5), "0001111111-25-000032"),
    )
    assert has_insider_buy_cluster(outside, as_of) is False


def test_bullish_blocked_without_insider_cluster(tmp_path):
    buys = (_buy("Only One", AS_OF - timedelta(days=5)),)
    inputs = make_inputs(buys=buys)
    body = payload(
        direction="bullish",
        confidence=0.9,
        rationale="Risks improved.",
        citations=[{"accession_no": NEW_ACC, "snippet": DOJ}],
    )
    signal = generate_signal(
        inputs, client(FakeTransport(body)), tmp_path / "r.jsonl", prefer_replay=False
    )
    assert signal.status == "ok"
    assert signal.direction == "neutral"
    assert signal.confidence <= CAP_NEUTRAL_CONFIDENCE
    assert "bullish capped" in signal.rationale


def test_bullish_allowed_with_insider_cluster(tmp_path):
    buys = (
        _buy("Alice", AS_OF - timedelta(days=5), "0001111111-25-000031"),
        _buy("Bob", AS_OF - timedelta(days=15), "0001111111-25-000032"),
    )
    removed = _diff().removed[0][:70]
    body = payload(
        direction="bullish",
        confidence=0.75,
        rationale="History of losses risk removed.",
        citations=[{"accession_no": OLD_ACC, "snippet": removed}],
    )
    signal = generate_signal(
        make_inputs(buys=buys),
        client(FakeTransport(body)),
        tmp_path / "r.jsonl",
        prefer_replay=False,
    )
    assert signal.status == "ok"
    assert signal.direction == "bullish"
    assert signal.confidence == 0.75


def test_thin_evidence_forces_low_confidence_neutral(tmp_path):
    thin = diff_paragraphs(
        ["Our business faces competition from larger rivals in every market we serve today."],
        ["Our business faces competition from larger rivals in every market we serve now."],
    )
    inputs = SignalInputs("ACME", NEW, OLD, thin, ())
    _, sources = build_prompt(inputs)
    snip = None
    acc = NEW_ACC
    for a, text in sources.items():
        if len(text) >= 12:
            snip = text[:50].strip()
            acc = a
            break
    if snip is None:
        pytest.skip("thin diff produced empty prompt sources")
    body = payload(
        direction="bearish",
        confidence=0.9,
        rationale="tiny wording change",
        citations=[{"accession_no": acc, "snippet": snip}],
    )
    signal = generate_signal(
        inputs, client(FakeTransport(body)), tmp_path / "r.jsonl", prefer_replay=False
    )
    assert signal.status == "ok"
    assert signal.direction == "neutral"
    assert signal.confidence <= THIN_MAX_CONFIDENCE
    assert "thin evidence" in signal.rationale.lower()


def test_fail_closed_paths_still_work(tmp_path):
    log = tmp_path / "r.jsonl"
    bad = payload(
        citations=[{"accession_no": NEW_ACC, "snippet": "Management expects record revenue next year."}]
    )
    s = generate_signal(make_inputs(), client(FakeTransport(bad)), log, prefer_replay=False)
    assert s.status == "no_signal" and "citation rejected" in s.reason
    s2 = generate_signal(
        make_inputs(),
        client(FakeTransport(exc=OllamaError("down"))),
        log,
        prefer_replay=False,
    )
    assert s2.status == "no_signal" and s2.reason.startswith("ollama unavailable")
    s3 = generate_signal(make_inputs(), client(FakeTransport("not-json")), log, prefer_replay=False)
    assert s3.status == "no_signal" and "invalid JSON" in s3.reason


def test_prompt_version_is_risk_diff_v2():
    assert PROMPT_VERSION == "risk-diff-v2"


def test_replay_cache_ignores_v1_prompt_version(tmp_path):
    path = tmp_path / "replay.jsonl"
    row = {
        "ticker": "ACME",
        "prompt_version": "risk-diff-v1",
        "new_accession": NEW_ACC,
        "old_accession": OLD_ACC,
        "insider_buy_accessions": [],
        "insider_buy_accepted_at": [],
        "signal": {
            "ticker": "ACME",
            "direction": "bullish",
            "confidence": 0.9,
            "rationale": "v1",
            "citations": [],
            "as_of": AS_OF.isoformat(),
            "model": "llama3.2",
            "status": "ok",
            "reason": "",
        },
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert find_cached_signal(path, "ACME", NEW_ACC, OLD_ACC, prompt_version="risk-diff-v2") is None
    assert find_cached_signal(path, "ACME", NEW_ACC, OLD_ACC, prompt_version="risk-diff-v1") is not None


def test_diff_exposes_unchanged_paragraphs_for_boilerplate_checks():
    diff = _diff()
    assert diff.unchanged_count == len(diff.unchanged) == 11
    assert "numerous risks" in diff.unchanged[0].lower()
