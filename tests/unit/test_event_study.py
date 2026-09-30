"""Event-study unit tests: point-in-time, metrics math, seeded random, replay never calls Ollama."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quant_intelligence.event_study.baselines import random_directions, signed_signal_return
from quant_intelligence.event_study.metrics import hit_rate, markdown_table, sharpe_ish, summarize
from quant_intelligence.event_study.prices import PriceStore, entry_session, horizon_session, raw_return, session_close_known
from quant_intelligence.event_study.replay import load_replay_signals
from quant_intelligence.event_study.study import EventStudyConfig, decide_gate, run_event_study
from quant_intelligence.signals.schema import RiskSignal

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "event_study"


def _write_prices(path: Path, rows: list[tuple[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    for day, close in rows:
        lines.append(f"{day},{close},{close},{close},{close},1000")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_entry_session_never_uses_unknown_same_day_close():
    days = [date(2025, 10, 30), date(2025, 10, 31), date(2025, 11, 3), date(2025, 11, 4)]
    mid = datetime(2025, 10, 31, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
    assert session_close_known(mid) is False
    assert entry_session(mid, days) == date(2025, 11, 3)
    after = datetime(2025, 10, 31, 21, 0, tzinfo=timezone.utc)  # 17:00 EDT
    assert session_close_known(after) is True
    assert entry_session(after, days) == date(2025, 10, 31)


def test_point_in_time_prices_after_as_of_do_not_leak_into_entry():
    """A price dated on as_of mid-session must not become the entry."""
    cache = FIXTURES / "prices"
    _write_prices(
        cache / "AAA.csv",
        [
            ("2025-01-02", 10.0),
            ("2025-01-03", 11.0),
            ("2025-01-06", 12.0),
            ("2025-01-07", 13.0),
            ("2025-01-08", 14.0),
            ("2025-01-09", 15.0),
            ("2025-01-10", 16.0),
            ("2025-01-13", 17.0),
        ],
    )
    store = PriceStore(cache, allow_network=False)
    as_of = datetime(2025, 1, 3, 15, 0, tzinfo=timezone.utc)
    days = store.trading_days("AAA")
    entry = entry_session(as_of, days)
    assert entry == date(2025, 1, 6)
    assert store.close_on("AAA", entry) == 12.0
    assert horizon_session(entry, days, 1) == date(2025, 1, 7)
    assert raw_return(12.0, 13.0) == pytest.approx(13 / 12 - 1)


def test_metrics_math_on_fixed_returns():
    values = [0.10, -0.05, 0.20, 0.0]
    assert hit_rate(values) == pytest.approx(0.5)
    stats = summarize("t", values, bh=[0.01, 0.01, 0.01, 0.01], random_mean=0.02)
    assert stats.n == 4
    assert stats.mean_return == pytest.approx(0.0625)
    assert stats.mean_excess_vs_bh == pytest.approx(0.0625 - 0.01)
    assert stats.mean_excess_vs_random == pytest.approx(0.0625 - 0.02)
    assert sharpe_ish(values) == pytest.approx(stats.sharpe_ish)


def test_random_baseline_is_seeded_and_deterministic():
    a = random_directions(5, 20, seed=123)
    b = random_directions(5, 20, seed=123)
    c = random_directions(5, 20, seed=999)
    assert a == b
    assert a != c
    assert all(d in {"bullish", "bearish"} for draw in a for d in draw)
    assert signed_signal_return("bullish", 0.1) == 0.1
    assert signed_signal_return("bearish", 0.1) == -0.1
    assert signed_signal_return("neutral", 0.1) is None


def test_replay_path_never_calls_ollama(tmp_path, monkeypatch):
    replay = tmp_path / "replay.jsonl"
    as_of = datetime(2025, 1, 3, 21, 0, tzinfo=timezone.utc).isoformat()
    signal = {
        "ticker": "AAA",
        "direction": "bullish",
        "confidence": 0.7,
        "rationale": "fixture",
        "citations": [{"accession_no": "0000999999-25-000101", "snippet": "We face material risks from competition in our primary markets today."}],
        "as_of": as_of,
        "model": "fixture-model",
        "status": "ok",
        "reason": "",
    }
    replay.write_text(json.dumps({"ticker": "AAA", "as_of": as_of, "signal": signal}) + "\n", encoding="utf-8")

    cache = tmp_path / "prices"
    _write_prices(
        cache / "AAA.csv",
        [
            ("2025-01-02", 10.0),
            ("2025-01-03", 10.5),
            ("2025-01-06", 11.0),
            ("2025-01-07", 11.5),
            ("2025-01-08", 12.0),
            ("2025-01-09", 12.5),
            ("2025-01-10", 13.0),
            ("2025-01-13", 13.5),
            ("2025-01-14", 14.0),
            ("2025-01-15", 14.5),
            ("2025-01-16", 15.0),
            ("2025-01-17", 15.5),
            ("2025-01-21", 16.0),
            ("2025-01-22", 16.5),
            ("2025-01-23", 17.0),
            ("2025-01-24", 17.5),
            ("2025-01-27", 18.0),
            ("2025-01-28", 18.5),
            ("2025-01-29", 19.0),
            ("2025-01-30", 19.5),
            ("2025-01-31", 20.0),
            ("2025-02-03", 20.5),
            ("2025-02-04", 21.0),
            ("2025-02-05", 21.5),
            ("2025-02-06", 22.0),
        ],
    )

    ollama = MagicMock()
    monkeypatch.setattr("quant_intelligence.signals.ollama.OllamaClient.chat", ollama)

    signals = load_replay_signals(replay)
    assert len(signals) == 1
    result = run_event_study(signals, PriceStore(cache, allow_network=False), mode="replay", price_source="fixture")
    assert result.events[0].error is None
    assert result.events[0].entry_date == "2025-01-03"
    assert result.reports[0].signal.n == 1
    ollama.assert_not_called()


def test_decide_gate_defaults_to_no_go_on_small_sample():
    from quant_intelligence.event_study.metrics import BucketStats, HorizonReport

    weak = HorizonReport(
        horizon="next_day",
        signal=BucketStats("signal", 2, 0.5, 0.01, None, 0.0, 0.0),
        buy_and_hold=BucketStats("buy-and-hold", 2, 0.5, 0.01, None),
        random=BucketStats("random", 500, 0.5, 0.005, None),
    )
    gate = decide_gate([weak], min_events=8)
    assert gate["decision"] == "NO-GO"


def test_full_study_on_fixture_replay_matches_expected_numbers(tmp_path):
    cache = tmp_path / "prices"
    _write_prices(
        cache / "AAA.csv",
        [(f"2025-03-{d:02d}", 100 + i) for i, d in enumerate(range(3, 32))],
    )
    _write_prices(
        cache / "BBB.csv",
        [(f"2025-03-{d:02d}", 50 + i * 0.5) for i, d in enumerate(range(3, 32))],
    )
    replay = tmp_path / "replay.jsonl"
    rows = []
    for ticker, as_of, direction, conf in (
        ("AAA", "2025-03-03T21:00:00+00:00", "bullish", 0.7),
        ("BBB", "2025-03-03T21:00:00+00:00", "bearish", 0.8),
        ("AAA", "2025-03-03T21:00:00+00:00", "neutral", 0.9),
    ):
        rows.append(
            json.dumps(
                {
                    "ticker": ticker,
                    "as_of": as_of,
                    "signal": {
                        "ticker": ticker,
                        "direction": direction,
                        "confidence": conf,
                        "rationale": "fixture",
                        "citations": []
                        if direction == "neutral"
                        else [{"accession_no": "0000999999-25-000101", "snippet": "We face material risks from competition in our primary markets today."}],
                        "as_of": as_of,
                        "model": "fixture",
                        "status": "ok",
                        "reason": "",
                    },
                }
            )
        )
    replay.write_text("\n".join(rows) + "\n", encoding="utf-8")
    signals = load_replay_signals(replay)
    result = run_event_study(
        signals,
        PriceStore(cache, allow_network=False),
        config=EventStudyConfig(horizons={"next_day": 1, "next_week": 5}, random_draws=50, random_seed=1),
        mode="replay",
        price_source="fixture",
    )
    day = next(r for r in result.reports if r.horizon == "next_day")
    assert day.signal.n == 2
    assert day.signal.mean_return == pytest.approx(0.0)
    assert day.buy_and_hold.mean_return == pytest.approx(0.01)
    table = markdown_table(result.reports)
    assert "next_day" in table and "signal" in table
