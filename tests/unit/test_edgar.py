import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quant_intelligence.edgar import (RiskFactorExtractionError, SecClient, SecConfigurationError, SecFetchError, diff_paragraphs,
                                      extract_item_1a, html_to_text, open_market_purchases, parse_form4, split_paragraphs)
from quant_intelligence.edgar.client import RateLimiter, filings_from_submissions, parse_acceptance
from quant_intelligence.edgar.form4 import Form4ParseError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
UA = "Test Org research test@example.com"
ACCEPTED = datetime(2025, 9, 17, 20, 0, 5, tzinfo=timezone.utc)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- Item 1A extraction -------------------------------------------------------------------------

def test_extract_item_1a_skips_toc_hidden_xbrl_and_stops_at_item_1b():
    section = extract_item_1a(fixture("tenk_2024.htm"))
    paragraphs = split_paragraphs(section)
    assert paragraphs[0].startswith("Our business is subject to numerous risks")
    assert paragraphs[-1].startswith("Our success depends on retaining key executives")
    assert "Unresolved Staff Comments" not in section and "hidden XBRL" not in section
    assert not any("Form 10-K |" in p for p in paragraphs), "page footers must be dropped"
    assert len(paragraphs) == 13


def test_extract_item_1a_uppercase_heading_ending_at_item_1b_before_1c():
    paragraphs = split_paragraphs(extract_item_1a(fixture("tenk_2025.htm")))
    assert paragraphs[-1].startswith("We are the subject of a pending Department of Justice investigation")
    assert len(paragraphs) == 13


def test_extract_item_1a_rejects_toc_only_documents():
    toc_only = b"<table><tr><td>Item 1A.</td><td>Risk Factors</td><td>5</td></tr><tr><td>Item 1B.</td><td>Unresolved Staff Comments</td></tr></table>"
    with pytest.raises(RiskFactorExtractionError, match="too short"):
        extract_item_1a(toc_only)
    with pytest.raises(RiskFactorExtractionError, match="no Item 1A"):
        extract_item_1a(b"<p>Item 7. Management's Discussion</p>")


def test_html_to_text_normalizes_entities_and_whitespace():
    assert html_to_text("<p>Risk&nbsp;&amp;\n  reward&#8217;s</p><div>next</div>") == "Risk & reward's\nnext"


# --- diffing ------------------------------------------------------------------------------------

def test_year_over_year_diff_classifies_added_removed_changed():
    old = split_paragraphs(extract_item_1a(fixture("tenk_2024.htm")))
    new = split_paragraphs(extract_item_1a(fixture("tenk_2025.htm")))
    diff = diff_paragraphs(old, new)
    assert [p[:40] for p in diff.added] == ["We are the subject of a pending Departme"]
    assert [p[:40] for p in diff.removed] == ["We have a history of net losses and may "]
    assert len(diff.changed) == 1 and "two contract manufacturers" in diff.changed[0].new and "single contract manufacturer" in diff.changed[0].old
    assert 0.75 <= diff.changed[0].ratio < 1
    assert diff.unchanged_count == 11
    assert 0.8 < diff.similarity < 1
    assert diff.summary()["added"] == 1


def test_diff_similarity_bounds():
    same = ["alpha beta gamma delta epsilon zeta eta theta"] * 2
    assert diff_paragraphs(same, same).similarity == 1.0
    disjoint = diff_paragraphs(["alpha beta gamma delta epsilon"], ["one two three four five six"])
    assert disjoint.similarity == 0.0 and len(disjoint.added) == 1 and len(disjoint.removed) == 1
    assert diff_paragraphs([], []).similarity == 1.0


# --- Form 4 ---------------------------------------------------------------------------------------

def test_form4_parses_open_market_purchase_only():
    txns = parse_form4(fixture("form4_purchase.xml"), "0001111111-25-000031", ACCEPTED)
    assert [t.code for t in txns] == ["P", "S", "M"]
    buys = open_market_purchases(txns)
    assert len(buys) == 1
    buy = buys[0]
    assert (buy.issuer_ticker, buy.filer, buy.filer_title, buy.shares, buy.price) == ("ACME", "Doe Jane", "Chief Executive Officer", 10000.0, 4.25)
    assert buy.accepted_at == ACCEPTED and buy.accession_no == "0001111111-25-000031"
    assert "open-market purchase of 10,000 shares at $4.25" in buy.describe()


def test_form4_malformed_xml_fails_closed():
    with pytest.raises(Form4ParseError):
        parse_form4(b"<ownershipDocument><broken>", "0001111111-25-000031", ACCEPTED)
    with pytest.raises(Form4ParseError):
        parse_form4(b"<html>not a form 4</html>", "0001111111-25-000031", ACCEPTED)
    with pytest.raises(ValueError):
        parse_form4(fixture("form4_purchase.xml"), "0001111111-25-000031", datetime(2025, 9, 17))


# --- client ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("ua", [None, "", "   ", "no contact info"])
def test_client_refuses_missing_or_contactless_user_agent(ua):
    with pytest.raises(SecConfigurationError):
        SecClient(ua, transport=lambda *a: (200, b""))


def test_client_rate_limit_cannot_exceed_sec_maximum():
    with pytest.raises(SecConfigurationError):
        RateLimiter(11)


def test_rate_limiter_spaces_requests():
    t = [0.0]; sleeps = []
    def sleep(s): sleeps.append(s); t[0] += s
    limiter = RateLimiter(5, clock=lambda: t[0], sleep=sleep)
    for _ in range(3): limiter.wait()
    assert sleeps == pytest.approx([0.2, 0.2])


def test_client_retries_429_and_5xx_with_backoff_then_succeeds(tmp_path):
    responses = iter([(429, b""), (503, b""), (200, b"ok")]); seen = []; sleeps = []
    def transport(url, headers, timeout): seen.append(headers["User-Agent"]); return next(responses)
    client = SecClient(UA, tmp_path, transport=transport, sleep=sleeps.append, backoff_seconds=1)
    assert client.get("https://www.sec.gov/x") == b"ok"
    assert seen == [UA] * 3
    assert [s for s in sleeps if s >= 1] == [1, 2]


def test_client_fails_closed_on_404_without_retry_and_on_exhausted_retries(tmp_path):
    calls = []
    client = SecClient(UA, tmp_path, transport=lambda u, h, t: calls.append(u) or (404, b""), sleep=lambda s: None)
    with pytest.raises(SecFetchError, match="HTTP 404"): client.get("https://www.sec.gov/missing")
    assert len(calls) == 1
    client = SecClient(UA, tmp_path, transport=lambda u, h, t: (500, b""), sleep=lambda s: None, max_retries=2)
    with pytest.raises(SecFetchError, match="HTTP 500"): client.get("https://www.sec.gov/down")


def test_client_caches_documents_keyed_by_ticker_acceptance_and_accession(tmp_path):
    submissions = json.loads(fixture("submissions_acme.json"))
    urls = []
    def transport(url, headers, timeout):
        urls.append(url)
        return (200, fixture("tenk_2025.htm"))
    client = SecClient(UA, tmp_path, transport=transport, sleep=lambda s: None)
    filings = filings_from_submissions("acme", 999999, submissions, ("10-K", "4"))
    assert [f.accession_no for f in filings][:3] == ["0000999999-25-000200", "0001111111-25-000031", "0000999999-25-000101"]
    assert "bad-accession" not in {f.accession_no for f in filings}
    tenk = next(f for f in filings if f.form == "10-K")
    client.fetch_document(tenk); client.fetch_document(tenk)
    assert urls == ["https://www.sec.gov/Archives/edgar/data/999999/000099999925000101/acme-20241231.htm"]
    stored = tmp_path / "ACME" / "10-K" / "20250303T213000Z_0000999999-25-000101"
    assert (stored / "acme-20241231.htm").is_file() and json.loads((stored / "filing.json").read_text())["accepted_at"] == "2025-03-03T21:30:00+00:00"
    form4 = next(f for f in filings if f.form == "4")
    assert form4.url.endswith("/form4_late.xml"), "raw XML, not the xsl-rendered view"


def test_parse_acceptance_is_timezone_aware_utc():
    assert parse_acceptance("2025-10-31T10:01:26.000Z") == datetime(2025, 10, 31, 10, 1, 26, tzinfo=timezone.utc)
    with pytest.raises(ValueError): parse_acceptance("2025-10-31T10:01:26")
