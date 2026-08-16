from datetime import date, timedelta
from math import inf, nan
import pytest
from quant_intelligence.data import DataValidationError, validate_bars
from quant_intelligence.models import Bar
from tests.fixtures.synthetic import bars

def test_duplicate_dates_rejected():
    one = bars([10])[0]; two = Bar(one.date, 10, 10, 10, 10, 1)
    with pytest.raises(DataValidationError): validate_bars([one, two])

@pytest.mark.parametrize("price", [0, -1])
def test_non_positive_prices_rejected(price):
    with pytest.raises(DataValidationError): validate_bars([Bar(date(2020,1,1), price, 1, 0.5, 1, 1)])

@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_non_finite_ohlcv_values_rejected(value):
    with pytest.raises(DataValidationError): validate_bars([Bar(date(2020, 1, 1), value, 1, 1, 1, 1)])

def test_non_increasing_dates_rejected():
    first = bars([10])[0]
    second = Bar(first.date - timedelta(days=1), 10, 10, 10, 10, 1)
    with pytest.raises(DataValidationError): validate_bars([first, second])

def test_negative_volume_rejected():
    with pytest.raises(DataValidationError): validate_bars([Bar(date(2020, 1, 1), 10, 10, 10, 10, -1)])

@pytest.mark.parametrize("value", [inf, -inf, nan])
def test_non_finite_volume_rejected(value):
    with pytest.raises(DataValidationError): validate_bars([Bar(date(2020, 1, 1), 10, 10, 10, 10, value)])

def test_missing_history_returns_cash():
    from quant_intelligence.strategies import SmaTrendStrategy
    assert SmaTrendStrategy(3).desired_position(bars([1, 2])) == "CASH"
