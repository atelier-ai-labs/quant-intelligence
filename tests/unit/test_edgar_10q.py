"""10-Q Item 1A extraction tests (expand-event-sample)."""

from pathlib import Path

import pytest

from quant_intelligence.edgar import RiskFactorExtractionError, extract_item_1a, split_paragraphs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_extract_item_1a_from_10q_fixture():
    section = extract_item_1a(fixture("tenq_2025q1.htm"))
    paragraphs = split_paragraphs(section)
    assert paragraphs[0].startswith("Our business is subject to numerous risks")
    assert any("Department of Justice" in p for p in paragraphs)
    assert not any("Form 10-Q |" in p for p in paragraphs)


def test_extract_item_1a_rejects_boilerplate_10q():
    with pytest.raises(RiskFactorExtractionError, match="too short"):
        extract_item_1a(fixture("tenq_short.htm"))
