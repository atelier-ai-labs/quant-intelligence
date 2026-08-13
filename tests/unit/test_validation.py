from datetime import date
import pytest
from quant_intelligence.data import DataValidationError, validate_bars
from quant_intelligence.models import Bar
from tests.fixtures.synthetic import bars

def test_duplicate_dates_rejected():
    one = bars([10])[0]; two = Bar(one.date, 10, 10, 10, 10, 1)
    with pytest.raises(DataValidationError): validate_bars([one, two])

def test_invalid_prices_rejected():
    with pytest.raises(DataValidationError): validate_bars([Bar(date(2020,1,1), 0, 1, 0, 1, 1)])

def test_missing_history_returns_cash():
    from quant_intelligence.strategies import SmaTrendStrategy
    assert SmaTrendStrategy(3).desired_position(bars([1, 2])) == "CASH"
