from dataclasses import replace
from quant_intelligence.backtest import run_backtest
from quant_intelligence.models import StrategySpec
from quant_intelligence.strategies import SmaTrendStrategy
from tests.fixtures.synthetic import bars

def spec(window=3, capital=1000, bps=0):
    return StrategySpec("test", "TEST", None, None, capital, signal_parameters={"window": window}, transaction_cost_bps=bps)

def test_manual_cash_long_cash_ledger():
    data = bars([10, 11, 12, 10, 9], [10, 10, 11, 12, 9])
    result = run_backtest(data, spec(), SmaTrendStrategy(3))
    assert [(t.side, t.date.day, t.quantity, t.execution_price) for t in result.trades] == [("BUY", 4, 83, 12), ("SELL", 5, 83, 9)]
    assert result.states[-1].equity == 751

def test_future_prices_do_not_change_previous_decisions():
    base = bars([10, 11, 12, 13, 12, 11])
    changed = bars([10, 11, 12, 13, 999, 999])
    a = run_backtest(base, spec(), SmaTrendStrategy(3)); b = run_backtest(changed, spec(), SmaTrendStrategy(3))
    assert [(t.date, t.side, t.quantity) for t in a.trades[:1]] == [(t.date, t.side, t.quantity) for t in b.trades[:1]]

def test_transaction_costs_and_repeatability():
    data = bars([10, 11, 12, 10], [10, 10, 11, 12])
    a = run_backtest(data, spec(bps=100), SmaTrendStrategy(3)); b = run_backtest(data, spec(bps=100), SmaTrendStrategy(3))
    assert a.metrics == b.metrics
    assert a.metrics["transaction_costs_paid"] > 0
