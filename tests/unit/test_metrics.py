from quant_intelligence.metrics import calculate_metrics
import pytest

def test_drawdown_and_cagr():
    result = calculate_metrics([100, 110, 99, 120], 100)
    assert result["maximum_drawdown"] == pytest.approx(-0.1)
    assert result["cagr"] is not None

def test_insufficient_sharpe_data_is_explicit():
    assert calculate_metrics([100], 100)["sharpe_ratio"] is None

def test_insufficient_series_does_not_fabricate_cagr():
    result = calculate_metrics([100], 100)
    assert result["cagr"] is None

def test_known_cagr_uses_252_observations_per_year():
    equity = [100 * 2 ** (index / 252) for index in range(253)]
    assert calculate_metrics(equity, 100)["cagr"] == pytest.approx(1.0)

def test_zero_volatility_sharpe_is_explicitly_undefined():
    assert calculate_metrics([100, 101, 102.01, 103.0301], 100)["sharpe_ratio"] is None

def test_known_sharpe_uses_daily_returns_and_sample_deviation():
    result = calculate_metrics([100, 101, 100, 102], 100)
    returns = [0.01, 100 / 101 - 1, 0.02]
    mean = sum(returns) / len(returns)
    sample_std = (sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)) ** 0.5
    assert result["sharpe_ratio"] == pytest.approx(mean / sample_std * 252 ** 0.5)

def test_investment_time_and_trade_cost_metrics_are_preserved():
    result = calculate_metrics([100, 101, 99], 100, costs=3.5, trades=2, invested_days=1)
    assert result["percentage_time_invested"] == pytest.approx(1 / 3)
    assert result["transaction_costs_paid"] == 3.5
    assert result["number_of_trades"] == 2

def test_metrics_are_deterministic():
    equity = [100, 105, 103, 110]
    assert calculate_metrics(equity, 100) == calculate_metrics(equity, 100)
