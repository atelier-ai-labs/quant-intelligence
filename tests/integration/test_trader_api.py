import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from quant_intelligence.api.main import create_app
from quant_intelligence.strategies import SmaTrendStrategy
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.broker import PaperBroker
from quant_intelligence.trading.cycle import TradingCycleService
from quant_intelligence.trading.market import FixtureMarketDataProvider
from quant_intelligence.trading.risk import RiskGate
from quant_intelligence.trading.status import OperationalStatus, StatusStore
from tests.integration.test_trading import NOW, bars


def seed_trader(tmp_path):
    audit_dir = tmp_path / "audit"
    broker_path = tmp_path / "broker.json"
    broker = PaperBroker(1000, transaction_cost_bps=0, state_path=broker_path)
    decision = TradingCycleService(
        strategy=SmaTrendStrategy(3), broker=broker,
        market_data=FixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])),
        risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(audit_dir),
    ).run("SYNTH", NOW)
    status_store = StatusStore(audit_dir / "status.json")
    status = OperationalStatus(state="running")
    status.update_from_decision(decision)
    status_store.save(status)
    return TestClient(create_app(tmp_path / "experiments", str(audit_dir), str(broker_path))), decision


def test_missing_trader_status_is_safe(tmp_path):
    client = TestClient(create_app(tmp_path / "experiments", str(tmp_path / "audit"), str(tmp_path / "broker.json")))
    response = client.get("/api/trader/status")
    assert response.status_code == 200
    assert response.json()["available"] is False


def test_status_and_portfolio_return_canonical_state(tmp_path):
    client, decision = seed_trader(tmp_path)
    status = client.get("/api/trader/status")
    assert status.status_code == 200
    assert status.json()["mode"] == "paper"
    assert status.json()["last_cycle_id"] == decision.cycle_id
    portfolio = client.get("/api/trader/portfolio")
    assert portfolio.status_code == 200
    assert portfolio.json()["equity"] == 1000
    assert portfolio.json()["positions"][0]["quantity"] == 76
    assert portfolio.json()["positions"][0]["market_value"] is None


def test_empty_portfolio_is_a_valid_read_only_response(tmp_path):
    audit_dir = tmp_path / "audit"
    status_store = StatusStore(audit_dir / "status.json")
    status_store.save(OperationalStatus(state="stopped", current_equity=1000, current_cash=1000))
    client = TestClient(create_app(tmp_path / "experiments", str(audit_dir), str(tmp_path / "broker.json")))
    portfolio = client.get("/api/trader/portfolio")
    assert portfolio.status_code == 200
    assert portfolio.json()["positions"] == []
    assert portfolio.json()["equity"] == 1000


def test_recent_decisions_are_newest_first_and_limited(tmp_path):
    client, decision = seed_trader(tmp_path)
    response = client.get("/api/trader/decisions?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["cycle_id"] == decision.cycle_id
    assert response.json()[0]["signal"] == "BUY"


def test_full_decision_detail_and_unknown_id(tmp_path):
    client, decision = seed_trader(tmp_path)
    detail = client.get(f"/api/trader/decisions/{decision.cycle_id}")
    assert detail.status_code == 200
    assert detail.json()["portfolio_before"]["equity"] == 1000
    assert detail.json()["fill"]["price"] == 13
    assert client.get("/api/trader/decisions/not-found").status_code == 404


def test_corrupt_status_and_broker_state_are_handled_without_crashing(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "status.json").write_text("{bad", encoding="utf-8")
    (tmp_path / "broker.json").write_text("{bad", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "experiments", str(audit_dir), str(tmp_path / "broker.json")))
    assert client.get("/api/trader/status").status_code == 503
    assert client.get("/api/trader/portfolio").status_code == 503


def test_corrupt_audit_is_skipped_and_detail_is_safe(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "corrupt.json").write_text("{bad", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "experiments", str(audit_dir), str(tmp_path / "broker.json")))
    assert client.get("/api/trader/decisions").json() == []
    assert client.get("/api/trader/decisions/corrupt").status_code == 503
