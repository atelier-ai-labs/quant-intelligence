from fastapi.testclient import TestClient

from quant_intelligence.api.main import create_app

CSV = "date,open,high,low,close,volume\n2020-01-01,10,10,10,10,1000\n2020-01-02,11,11,11,11,1000\n2020-01-03,12,12,12,12,1000\n2020-01-04,10,13,10,13,1000\n"

def client_with_data(tmp_path):
    data_path = tmp_path / "synthetic.csv"
    data_path.write_text(CSV, encoding="utf-8")
    return TestClient(create_app(tmp_path / "experiments")), data_path

def request_payload(data_path):
    return {"symbol": "SYNTH", "strategy": "sma_trend", "parameters": {"window": 3}, "initial_capital": 1000, "transaction_cost_bps": 0, "benchmark": "buy_and_hold", "data_path": str(data_path)}

def test_health_endpoint():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "quant-intelligence"}

def test_empty_experiment_store_lists_empty(tmp_path):
    client = TestClient(create_app(tmp_path / "experiments"))
    assert client.get("/api/experiments").json() == []

def test_post_persists_and_gets_canonical_result(tmp_path):
    client, data_path = client_with_data(tmp_path)
    created = client.post("/api/experiments", json=request_payload(data_path))
    assert created.status_code == 201
    body = created.json()
    experiment_id = body["experiment_id"]
    assert body["result"]["metadata"]["experiment_id"] == experiment_id
    assert body["result"]["states"][-1]["equity"] == 1300
    listed = client.get("/api/experiments")
    assert listed.status_code == 200
    assert listed.json()[0]["experiment_id"] == experiment_id
    detail = client.get(f"/api/experiments/{experiment_id}")
    assert detail.status_code == 200
    assert detail.json() == body["result"]

def test_missing_experiment_returns_404(tmp_path):
    client = TestClient(create_app(tmp_path / "experiments"))
    response = client.get("/api/experiments/not-found")
    assert response.status_code == 404

def test_invalid_strategy_is_client_error(tmp_path):
    client, data_path = client_with_data(tmp_path)
    payload = request_payload(data_path); payload["strategy"] = "unsupported"
    response = client.post("/api/experiments", json=payload)
    assert response.status_code == 422
    assert "unsupported strategy" in response.json()["detail"]

def test_malformed_request_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path / "experiments"))
    response = client.post("/api/experiments", json={"symbol": "SYNTH"})
    assert response.status_code == 422
