import json
import socket
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_intelligence.edgar import diff_paragraphs, extract_item_1a, parse_form4, split_paragraphs
from quant_intelligence.edgar.client import FilingRef
from quant_intelligence.signals import (LLMSignalPayload, OllamaClient, OllamaError, RiskSignal, SignalInputs, build_prompt, generate_signal,
                                        route_signal, signal_to_intent, validate_citations)
from quant_intelligence.trading.models import PortfolioSnapshot, Position, SignalAction
from quant_intelligence.trading.risk import RiskConfig, RiskGate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
NEW_ACC, OLD_ACC, F4_ACC = "0000999999-25-000101", "0000999999-24-000090", "0001111111-25-000031"
AS_OF = datetime(2025, 12, 1, 21, 30, tzinfo=timezone.utc)
NEW = FilingRef("ACME", 999999, "10-K", NEW_ACC, AS_OF, date(2025, 12, 1), "acme-20251130.htm")
OLD = FilingRef("ACME", 999999, "10-K", OLD_ACC, datetime(2024, 12, 2, 21, 30, tzinfo=timezone.utc), date(2024, 12, 2), "acme-20241130.htm")
DOJ = "We are the subject of a pending Department of Justice investigation relating to export controls"


def make_inputs(buy_accepted_at: datetime | None = datetime(2025, 9, 17, 20, 0, tzinfo=timezone.utc)) -> SignalInputs:
    old = split_paragraphs(extract_item_1a((FIXTURES / "tenk_2024.htm").read_bytes()))
    new = split_paragraphs(extract_item_1a((FIXTURES / "tenk_2025.htm").read_bytes()))
    buys = ()
    if buy_accepted_at is not None:
        buys = tuple(t for t in parse_form4((FIXTURES / "form4_purchase.xml").read_bytes(), F4_ACC, buy_accepted_at) if t.is_open_market_purchase)
    return SignalInputs("ACME", NEW, OLD, diff_paragraphs(old, new), buys)


class FakeTransport:
    """Stands in for Ollama's /api/chat. No network."""
    def __init__(self, content=None, exc=None, response=None):
        self.content, self.exc, self.response, self.calls = content, exc, response, []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.exc: raise self.exc
        if self.response is not None: return self.response
        return {"model": payload["model"], "message": {"role": "assistant", "content": self.content}, "done": True}


def client(transport) -> OllamaClient:
    return OllamaClient("http://127.0.0.1:11434", "llama3.2", 5, transport=transport)


def payload(**overrides) -> str:
    data = {"direction": "bearish", "confidence": 0.8, "rationale": "A new DOJ investigation risk factor was added.",
            "citations": [{"accession_no": NEW_ACC, "snippet": DOJ}]}
    data.update(overrides)
    return json.dumps(data)


def read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- schema ---------------------------------------------------------------------------------------

def test_payload_schema_accepts_good_and_rejects_bad_payloads():
    assert LLMSignalPayload.model_validate_json(payload()).direction == "bearish"
    assert LLMSignalPayload.model_validate_json(payload(direction="neutral", citations=[])).citations == []
    for bad in (payload(direction="very bullish"), payload(confidence=1.5), payload(confidence=-0.1), payload(citations=[]),
                payload(citations=[{"accession_no": "123", "snippet": DOJ}]), payload(extra_field=1), json.dumps({"direction": "bullish"})):
        with pytest.raises(ValidationError):
            LLMSignalPayload.model_validate_json(bad)


def test_citation_validation_requires_verbatim_snippet_and_known_accession():
    inputs = make_inputs()
    _, sources = build_prompt(inputs)
    good = LLMSignalPayload.model_validate_json(payload())
    assert validate_citations(good, sources) is None
    spaced = LLMSignalPayload.model_validate_json(payload(citations=[{"accession_no": NEW_ACC, "snippet": DOJ.replace(" ", "\n  ")}]))
    assert validate_citations(spaced, sources) is None, "whitespace differences are tolerated"
    invented = LLMSignalPayload.model_validate_json(payload(citations=[{"accession_no": NEW_ACC, "snippet": "The CEO has resigned amid an accounting scandal"}]))
    assert "not found" in validate_citations(invented, sources)
    wrong_filing = LLMSignalPayload.model_validate_json(payload(citations=[{"accession_no": OLD_ACC, "snippet": DOJ}]))
    assert "not found" in validate_citations(wrong_filing, sources), "snippet must come from the cited filing's text"
    unknown = LLMSignalPayload.model_validate_json(payload(citations=[{"accession_no": "0000000000-25-000001", "snippet": DOJ}]))
    assert "unknown accession" in validate_citations(unknown, sources)


def test_prompt_contains_only_labelled_diff_text_and_insider_buys():
    messages, sources = build_prompt(make_inputs())
    user = messages[1]["content"]
    assert f"[ADDED in 10-K {NEW_ACC}]" in user and f"[REMOVED, was in 10-K {OLD_ACC}]" in user and f"[FORM 4 {F4_ACC}]" in user
    assert set(sources) == {NEW_ACC, OLD_ACC, F4_ACC}
    assert "Doe Jane" in sources[F4_ACC]


# --- end-to-end signal generation (mocked Ollama) ----------------------------------------------

def test_valid_grounded_response_produces_signal_and_replay_record(tmp_path):
    transport = FakeTransport(payload())
    log = tmp_path / "replay.jsonl"
    signal = generate_signal(make_inputs(), client(transport), log, now=lambda: AS_OF + timedelta(days=1))
    assert (signal.status, signal.direction, signal.confidence, signal.as_of) == ("ok", "bearish", 0.8, AS_OF)
    assert signal.citations[0].accession_no == NEW_ACC
    url, sent, timeout = transport.calls[0]
    assert url == "http://127.0.0.1:11434/api/chat" and sent["stream"] is False and sent["format"]["type"] == "object" and sent["options"]["temperature"] == 0
    [record] = read_log(log)
    assert record["raw_output"] == payload() and record["parsed"]["direction"] == "bearish" and record["model"] == "llama3.2"
    assert record["messages"][1]["content"] == sent["messages"][1]["content"] and record["signal"]["status"] == "ok"


@pytest.mark.parametrize("transport,reason", [
    (FakeTransport(exc=OllamaError("Ollama unreachable or timed out: [Errno 111] Connection refused")), "ollama unavailable"),
    (FakeTransport(exc=socket.timeout("timed out")), "ollama unavailable"),
    (FakeTransport(exc=ConnectionRefusedError(111, "refused")), "ollama unavailable"),
    (FakeTransport(exc=RuntimeError("boom")), "ollama call failed"),
    (FakeTransport(response={"error": "model 'llama3.2' not found"}), "ollama unavailable"),
    (FakeTransport(content="Sure! Here is my analysis: bullish!!"), "invalid JSON"),
    (FakeTransport(content="{\"direction\": \"bullish\", \"confidence\": 0.9"), "invalid JSON"),
    (FakeTransport(content=payload(confidence=3)), "schema validation failed"),
    (FakeTransport(content=payload(citations=[{"accession_no": NEW_ACC, "snippet": "Management expects record revenue next year."}])), "citation rejected"),
    (FakeTransport(content=payload(citations=[{"accession_no": "0000000000-25-000001", "snippet": DOJ}])), "citation rejected"),
])
def test_every_failure_mode_fails_closed_to_neutral_no_signal(tmp_path, transport, reason):
    log = tmp_path / "replay.jsonl"
    signal = generate_signal(make_inputs(), client(transport), log)
    assert (signal.status, signal.direction, signal.confidence) == ("no_signal", "neutral", 0.0)
    assert signal.reason.startswith(reason), signal.reason
    assert read_log(log)[0]["signal"]["reason"] == signal.reason
    intent, why = signal_to_intent(signal)
    assert intent is None and why.startswith("no intent")


def test_real_socket_to_closed_port_fails_closed(tmp_path):
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    signal = generate_signal(make_inputs(), OllamaClient(f"http://127.0.0.1:{port}", "llama3.2", 2), tmp_path / "r.jsonl")
    assert signal.status == "no_signal" and signal.reason.startswith("ollama unavailable")


def test_replay_log_failure_discards_signal(tmp_path):
    blocker = tmp_path / "not_a_dir"; blocker.write_text("x")
    signal = generate_signal(make_inputs(), client(FakeTransport(payload())), blocker / "replay.jsonl")
    assert signal.status == "no_signal" and "replay log write failed" in signal.reason


def test_empty_diff_skips_model(tmp_path):
    inputs = make_inputs()
    inputs = replace(inputs, diff=diff_paragraphs(["same paragraph text here"], ["same paragraph text here"]))
    transport = FakeTransport(payload())
    signal = generate_signal(inputs, client(transport), tmp_path / "r.jsonl")
    assert signal.status == "no_signal" and "empty" in signal.reason and transport.calls == []


# --- point in time ------------------------------------------------------------------------------

def test_no_signal_uses_form4_accepted_after_as_of(tmp_path):
    future_buy = make_inputs(buy_accepted_at=AS_OF + timedelta(seconds=1))
    transport = FakeTransport(payload())
    signal = generate_signal(future_buy, client(transport), tmp_path / "r.jsonl")
    assert signal.status == "no_signal" and signal.reason.startswith("point-in-time violation") and transport.calls == []
    same_instant = make_inputs(buy_accepted_at=AS_OF)
    assert generate_signal(same_instant, client(FakeTransport(payload())), tmp_path / "r.jsonl").status == "ok"


def test_no_signal_uses_prior_10k_accepted_after_as_of(tmp_path):
    inputs = replace(make_inputs(), old_filing=replace(OLD, accepted_at=AS_OF + timedelta(days=1)))
    signal = generate_signal(inputs, client(FakeTransport(payload())), tmp_path / "r.jsonl")
    assert signal.status == "no_signal" and "point-in-time" in signal.reason


def test_signal_as_of_is_filing_acceptance_and_must_be_aware():
    assert make_inputs().as_of == AS_OF
    with pytest.raises(ValidationError):
        RiskSignal(ticker="ACME", direction="neutral", confidence=0, rationale="", as_of=datetime(2025, 1, 1), model="m", status="ok")
    with pytest.raises(ValidationError):
        RiskSignal(ticker="ACME", direction="bullish", confidence=0.9, rationale="", as_of=AS_OF, model="m", status="no_signal")


# --- signal -> OrderIntent -> RiskGate --------------------------------------------------------

def ok_signal(direction="bullish", confidence=0.8) -> RiskSignal:
    return RiskSignal(ticker="ACME", direction=direction, confidence=confidence, rationale="r",
                      citations=(LLMSignalPayload.model_validate_json(payload()).citations[0],), as_of=AS_OF, model="llama3.2", status="ok")


def snapshot(shares=0, cash=10_000.0, ts=AS_OF + timedelta(days=1)) -> PortfolioSnapshot:
    positions = (Position("ACME", shares, 4.0),) if shares else ()
    return PortfolioSnapshot(ts, cash, positions, shares * 4.0, cash + shares * 4.0, 0.0)


def test_signal_to_intent_mapping_rules():
    intent, _ = signal_to_intent(ok_signal("bullish"), quantity=3)
    assert (intent.symbol, intent.side, intent.quantity) == ("ACME", SignalAction.BUY, 3) and NEW_ACC in intent.reason
    assert signal_to_intent(ok_signal("bullish", 0.59))[0] is None
    assert signal_to_intent(ok_signal("neutral", 0.99))[0] is None
    assert signal_to_intent(ok_signal("bearish"), owned_shares=0)[0] is None, "no shorting"
    sell, _ = signal_to_intent(ok_signal("bearish"), quantity=5, owned_shares=2)
    assert (sell.side, sell.quantity) == (SignalAction.SELL, 2)
    assert signal_to_intent(ok_signal("bullish"), quantity=0)[0] is None


def test_route_signal_goes_through_risk_gate_without_broker(monkeypatch):
    evaluated = []
    gate = RiskGate(RiskConfig(max_position_allocation=0.25, max_order_notional=2500))
    original = gate.evaluate
    monkeypatch.setattr(gate, "evaluate", lambda intent, snap, price: evaluated.append(intent) or original(intent, snap, price))
    approved = route_signal(ok_signal(), gate, snapshot(), 4.25, quantity=10)
    assert approved.decision.approved and approved.intent.side == SignalAction.BUY and evaluated == [approved.intent]
    too_big = route_signal(ok_signal(), gate, snapshot(), 4.25, quantity=1000)
    assert not too_big.decision.approved and too_big.decision.reason == "order exceeds maximum notional"
    no_price = route_signal(ok_signal(), gate, snapshot(), None)
    assert not no_price.decision.approved and "no reference price" in no_price.decision.reason
    neutral = route_signal(RiskSignal.no_signal("ACME", AS_OF, "llama3.2", "ollama unavailable: down"), gate, snapshot(), 4.25)
    assert neutral.intent is None and not neutral.decision.approved
    lookahead = route_signal(ok_signal(), gate, snapshot(ts=AS_OF - timedelta(seconds=1)), 4.25)
    assert lookahead.intent is None and "look-ahead" in lookahead.decision.reason
    assert len(evaluated) == 2  # only intents with a price reach RiskGate.evaluate; route_signal has no broker parameter


def test_signal_and_edgar_packages_never_import_execution_layers():
    import ast
    import quant_intelligence
    forbidden = {"quant_intelligence.trading.broker", "quant_intelligence.trading.alpaca", "quant_intelligence.trading.cycle",
                 "quant_intelligence.trading.autonomous", "quant_intelligence.trading.runtime", "alpaca"}
    root = Path(quant_intelligence.__file__).parent
    for source in [*root.joinpath("signals").glob("*.py"), *root.joinpath("edgar").glob("*.py")]:
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n in forbidden or n.startswith("alpaca.") for n in names), f"{source.name} imports {names}"
            if isinstance(node, ast.Attribute): assert node.attr not in {"submit_order", "submit"}, f"{source.name} references {node.attr}"
