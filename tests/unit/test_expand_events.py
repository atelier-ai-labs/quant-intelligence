"""Expanded event-sample helpers: multi-pair selection, 10-Q pairing, replay cache."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quant_intelligence.edgar.client import FilingRef
from quant_intelligence.event_study.replay import find_cached_signal, load_replay_signals
from quant_intelligence.signals.pipeline import UniverseConfig, _prior_extractable, _yoy_prior_10q, load_universe
from quant_intelligence.signals.schema import RiskSignal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


def _ref(form: str, accession: str, accepted: str, filing: str) -> FilingRef:
    return FilingRef(
        "ACME",
        999999,
        form,
        accession,
        datetime.fromisoformat(accepted.replace("Z", "+00:00")),
        date.fromisoformat(filing),
        "doc.htm",
    )


def test_universe_loads_expand_fields():
    cfg = load_universe("config/edgar_universe.toml")
    assert isinstance(cfg, UniverseConfig)
    assert len(cfg.tickers) >= 30
    assert cfg.max_10k_pairs >= 1
    assert cfg.include_10q is True


def test_yoy_prior_10q_picks_same_season_filing():
    q1_2025 = _ref("10-Q", "0000999999-25-000050", "2025-05-01T20:00:00Z", "2025-05-01")
    q1_2024 = _ref("10-Q", "0000999999-24-000050", "2024-05-02T20:00:00Z", "2024-05-02")
    q3_2024 = _ref("10-Q", "0000999999-24-000080", "2024-11-01T20:00:00Z", "2024-11-01")
    earlier = [q1_2024, q3_2024]
    assert _yoy_prior_10q(q1_2025, earlier) == q1_2024


def test_prior_extractable_falls_back_to_10k():
    tenk = _ref("10-K", "0000999999-24-000090", "2024-03-01T21:30:00Z", "2024-03-01")
    tenq = _ref("10-Q", "0000999999-24-000050", "2024-05-02T20:00:00Z", "2024-05-02")
    paras = {tenk.accession_no: ["enough text to count as extractable risk factor body"], tenq.accession_no: None}
    got = _prior_extractable(tenq, [tenk], paras)
    assert got is not None
    prior, text, kind = got
    assert prior == tenk and kind == "10q_vs_10k" and text


def test_find_cached_signal_matches_accession_pair(tmp_path):
    path = tmp_path / "replay.jsonl"
    signal = {
        "ticker": "OPEN",
        "direction": "bullish",
        "confidence": 0.7,
        "rationale": "x",
        "citations": [],
        "as_of": "2026-02-19T21:25:06+00:00",
        "model": "llama3.2",
        "status": "ok",
        "reason": "",
    }
    row = {
        "ticker": "OPEN",
        "new_accession": "0001801169-26-000010",
        "old_accession": "0001801169-25-000017",
        "as_of": signal["as_of"],
        "signal": signal,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    found = find_cached_signal(path, "OPEN", "0001801169-26-000010", "0001801169-25-000017")
    assert found is not None and found.direction == "bullish" and found.confidence == 0.7
    assert find_cached_signal(path, "OPEN", "0001801169-26-000010", "0000000000-00-000000") is None


def test_load_replay_signals_dedupes_by_new_accession(tmp_path):
    path = tmp_path / "replay.jsonl"
    def row(conf: float) -> str:
        return json.dumps(
            {
                "ticker": "PLUG",
                "new_accession": "0001104659-26-022286",
                "old_accession": "0001558370-25-002049",
                "signal": {
                    "ticker": "PLUG",
                    "direction": "bullish",
                    "confidence": conf,
                    "rationale": "r",
                    "citations": [],
                    "as_of": "2026-03-02T21:30:29+00:00",
                    "model": "llama3.2",
                    "status": "ok",
                    "reason": "",
                },
            }
        )
    path.write_text(row(0.6) + "\n" + row(0.8) + "\n", encoding="utf-8")
    signals = load_replay_signals(path)
    assert len(signals) == 1 and signals[0].confidence == 0.8
