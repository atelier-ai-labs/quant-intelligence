from quant_intelligence.metrics import calculate_metrics
import pytest

def test_drawdown_and_cagr():
    result = calculate_metrics([100, 110, 99, 120], 100)
    assert result["maximum_drawdown"] == pytest.approx(-0.1)
    assert result["cagr"] is not None

def test_insufficient_sharpe_data_is_explicit():
    assert calculate_metrics([100], 100)["sharpe_ratio"] is None
