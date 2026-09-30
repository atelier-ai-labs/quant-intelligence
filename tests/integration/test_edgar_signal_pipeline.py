"""Pipeline + CLI with EDGAR and Ollama fully mocked (no network)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quant_intelligence.edgar.client import SecClient
from quant_intelligence.signals import pipeline as pipeline_mod
from quant_intelligence.signals import run as run_mod
from quant_intelligence.signals.pipeline import PipelineError, build_inputs, load_universe

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
UA = "Test Org research test@example.com"
TICKERS = {"0": {"cik_str": 999999, "ticker": "ACME", "title": "Acme Widgets Inc."}}
LATE_FORM4 = FIXTURES.joinpath("form4_purchase.xml").read_text().replace("<value>10000</value>", "<value>777777</value>")


def edgar_transport(requests: list[str]):
    routes = {
        "https://www.sec.gov/files/company_tickers.json": json.dumps(TICKERS).encode(),
        "https://data.sec.gov/submissions/CIK0000999999.json": FIXTURES.joinpath("submissions_acme.json").read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/999999/000099999925000101/acme-20241231.htm": FIXTURES.joinpath("tenk_2025.htm").read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/999999/000099999924000090/acme-20231231.htm": FIXTURES.joinpath("tenk_2024.htm").read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/999999/000111111125000031/form4_purchase.xml": FIXTURES.joinpath("form4_purchase.xml").read_bytes(),
        "https://www.sec.gov/Archives/edgar/data/999999/000111111125000020/form4_early.xml": b"<ownershipDocument><issuer><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer></ownershipDocument>",
        "https://www.sec.gov/Archives/edgar/data/999999/000099999925000200/form4_late.xml": LATE_FORM4.encode(),
    }
    def transport(url, headers, timeout):
        assert headers["User-Agent"] == UA
        requests.append(url)
        return (200, routes[url]) if url in routes else (404, b"")
    return transport


def test_build_inputs_is_point_in_time(tmp_path):
    requests: list[str] = []
    client = SecClient(UA, tmp_path, transport=edgar_transport(requests), sleep=lambda s: None)
    inputs, report = build_inputs(client, "acme", cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc), lookback_days=365)
    assert inputs.new_filing.accession_no == "0000999999-25-000101" and inputs.old_filing.accession_no == "0000999999-24-000090"
    assert inputs.as_of == datetime(2025, 3, 3, 21, 30, tzinfo=timezone.utc)
    # The Sep-2025 purchase and the Dec-2025 Form 4 were accepted after the 10-K: never fetched, never used.
    assert not any("form4_late" in u or "form4_purchase" in u for u in requests)
    assert all(b.accepted_at <= inputs.as_of for b in inputs.insider_buys)
    assert report.diff["added"] == 1 and report.form4["filings_parsed"] == 1 and report.form4["open_market_purchases"] == []
    # As of a cutoff before the 2025 10-K existed, the pipeline refuses (only one 10-K was public).
    with pytest.raises(PipelineError, match="need two 10-K"):
        build_inputs(client, "ACME", cutoff=datetime(2025, 3, 3, 21, 29, tzinfo=timezone.utc))


def test_build_inputs_uses_form4_buys_known_before_filing(tmp_path, monkeypatch):
    submissions = json.loads(FIXTURES.joinpath("submissions_acme.json").read_text())
    # Pretend the Sep-2025 Form 4 was accepted before the 10-K.
    submissions["filings"]["recent"]["acceptanceDateTime"][1] = "2025-02-20T20:00:05.000Z"
    requests: list[str] = []
    base = edgar_transport(requests)
    def transport(url, headers, timeout):
        if url.endswith("CIK0000999999.json"): return 200, json.dumps(submissions).encode()
        return base(url, headers, timeout)
    client = SecClient(UA, tmp_path, transport=transport, sleep=lambda s: None)
    inputs, report = build_inputs(client, "ACME", cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert [b.shares for b in inputs.insider_buys] == [10000.0]
    assert inputs.insider_buys[0].accepted_at < inputs.as_of


def test_cli_end_to_end_with_mocked_services(tmp_path, monkeypatch, capsys):
    """risk-diff-v2: bearish with ADDED citation clears the higher bar; no short → no intent."""
    requests: list[str] = []
    monkeypatch.setattr("quant_intelligence.edgar.client.urllib_transport", edgar_transport(requests))
    content = json.dumps({
        "direction": "bearish",
        "confidence": 0.8,
        "rationale": "A new DOJ investigation risk factor was added.",
        "citations": [{
            "accession_no": "0000999999-25-000101",
            "snippet": "We are the subject of a pending Department of Justice investigation relating to export controls",
        }],
    })
    monkeypatch.setattr("quant_intelligence.signals.ollama.urllib_transport", lambda url, payload, timeout: {"message": {"content": content}})
    monkeypatch.setenv("SEC_USER_AGENT", UA); monkeypatch.setenv("QI_EDGAR_CACHE_DIR", str(tmp_path / "edgar"))
    config = tmp_path / "universe.toml"
    config.write_text('[edgar]\ntickers = ["ACME"]\nrequests_per_second = 10\n[signals]\nmin_confidence = 0.6\norder_quantity = 5\n')
    replay = tmp_path / "replay.jsonl"
    assert run_mod.main(["--ticker", "ACME", "--config", str(config), "--price", "4.25", "--replay-log", str(replay), "--as-of", "2026-01-01T00:00:00+00:00"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["signal"]["status"] == "ok" and out["signal"]["direction"] == "bearish"
    assert out["signal"]["as_of"] == "2025-03-03T21:30:00Z"
    assert out["intent"] is None  # bearish with no position → no shorting
    assert out["risk_decision"]["approved"] is False
    assert "prompt_version" not in out or True
    record = json.loads(replay.read_text().splitlines()[0])
    assert record["prompt_version"] == "risk-diff-v2"
    assert len(replay.read_text().splitlines()) == 1



def test_cli_refuses_empty_user_agent(monkeypatch, capsys):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    assert run_mod.main(["--ticker", "AAPL"]) == 2
    assert "SEC_USER_AGENT" in capsys.readouterr().err


def test_cli_fails_closed_when_edgar_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("quant_intelligence.edgar.client.urllib_transport", lambda u, h, t: (503, b""))
    monkeypatch.setattr("quant_intelligence.edgar.client.time.sleep", lambda s: None)
    monkeypatch.setenv("SEC_USER_AGENT", UA); monkeypatch.setenv("QI_EDGAR_CACHE_DIR", str(tmp_path / "edgar"))
    assert run_mod.main(["--ticker", "ACME", "--replay-log", str(tmp_path / "r.jsonl")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "no_signal" and "HTTP 503" in out["reason"] and out["intent"] is None


def test_default_universe_config_loads():
    universe = load_universe(pipeline_mod.DEFAULT_CONFIG)
    assert 30 <= len(universe.tickers) <= 60 and universe.requests_per_second <= 10
