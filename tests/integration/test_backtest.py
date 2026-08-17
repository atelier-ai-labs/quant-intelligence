from dataclasses import asdict
import json
import hashlib
import pytest
from quant_intelligence.backtest import run_backtest
from quant_intelligence.backtest.engine import buy_and_hold
from quant_intelligence.experiments import SCHEMA_VERSION, load_result, save_result
from quant_intelligence import __version__
from quant_intelligence.models import StrategySpec
from quant_intelligence.portfolio import BasisPointCostModel
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

def test_transaction_costs_are_applied_to_buys_and_sells():
    result = run_backtest(bars([10, 11, 12, 10, 9], [10, 10, 11, 12, 9]), spec(bps=100), SmaTrendStrategy(3))
    assert [trade.transaction_cost for trade in result.trades] == pytest.approx([9.84, 7.38])
    assert result.metrics["transaction_costs_paid"] == pytest.approx(17.22)

def test_repeated_identical_long_signals_generate_one_entry():
    result = run_backtest(bars([10, 11, 12, 13, 14]), spec(), SmaTrendStrategy(3))
    assert [(trade.side, trade.date.day) for trade in result.trades] == [("BUY", 4)]

def test_cash_long_cash_long_transitions_follow_signal_changes():
    result = run_backtest(bars([10, 11, 12, 10, 9, 10, 11, 12]), spec(), SmaTrendStrategy(3))
    assert [(trade.side, trade.date.day) for trade in result.trades] == [("BUY", 4), ("SELL", 5), ("BUY", 7)]

def test_final_open_position_is_marked_to_market_and_reconciles():
    result = run_backtest(bars([10, 11, 12, 13], [10, 11, 12, 10]), spec(), SmaTrendStrategy(3))
    state = result.states[-1]
    assert state.shares == 100
    assert state.equity == pytest.approx(state.cash + state.asset_value)
    assert state.equity == pytest.approx(1300)

def test_insufficient_cash_prevents_invalid_buy():
    result = run_backtest(bars([10, 11, 12], [12, 12, 12]), spec(capital=10), SmaTrendStrategy(3))
    assert result.trades == []
    assert result.states[-1].cash == 10
    assert result.states[-1].shares == 0

def test_exact_affordability_boundary_includes_transaction_costs():
    result = run_backtest(bars([10, 11, 12, 12], [10, 10, 10, 10]), spec(capital=1000, bps=100), SmaTrendStrategy(3))
    trade = result.trades[0]
    assert trade.quantity == 99
    assert trade.cash_after == pytest.approx(0.1)
    assert all(state.cash >= 0 for state in result.states)

def test_no_trade_scenario_preserves_capital():
    result = run_backtest(bars([12, 11, 10, 9, 8]), spec(capital=1000), SmaTrendStrategy(3))
    assert result.trades == []
    assert all(state.equity == 1000 for state in result.states)

def test_future_changes_do_not_affect_multiple_prior_decisions():
    base = bars([10, 11, 12, 10, 9, 10, 11, 12])
    altered = bars([10, 11, 12, 10, 9, 10, 999, 999])
    first = run_backtest(base, spec(), SmaTrendStrategy(3))
    second = run_backtest(altered, spec(), SmaTrendStrategy(3))
    cutoff = 6
    assert [(t.date, t.side, t.quantity) for t in first.trades if t.date.day < cutoff] == [(t.date, t.side, t.quantity) for t in second.trades if t.date.day < cutoff]
    assert [asdict(state) for state in first.states[:cutoff-1]] == [asdict(state) for state in second.states[:cutoff-1]]

def test_mismatched_runtime_strategy_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        run_backtest(bars([10, 11, 12, 13]), spec(window=3), SmaTrendStrategy(2))

def test_buy_and_hold_applies_costs_sizing_and_reconciles():
    metrics, equity = buy_and_hold(bars([10, 11, 12], [10, 10, 10]), 1000, BasisPointCostModel(100))
    assert metrics["number_of_trades"] == 1
    assert metrics["transaction_costs_paid"] == pytest.approx(9.9)
    assert metrics["percentage_time_invested"] == 1
    assert equity[-1].equity == pytest.approx(1188.1)

def test_benchmark_handles_insufficient_capital():
    metrics, equity = buy_and_hold(bars([10, 11, 12]), 5, BasisPointCostModel(5))
    assert metrics["number_of_trades"] == 0
    assert metrics["percentage_time_invested"] == 0
    assert [point.equity for point in equity] == [5, 5, 5]

def test_benchmark_is_independent_of_strategy_decisions():
    data = bars([10, 11, 12, 10, 9, 10, 11])
    longish = run_backtest(data, spec(window=3), SmaTrendStrategy(3))
    slower = run_backtest(data, spec(window=4), SmaTrendStrategy(4))
    assert longish.benchmark_metrics == slower.benchmark_metrics
    assert longish.benchmark_equity == slower.benchmark_equity

def test_persistence_round_trip_and_audit_metadata(tmp_path):
    result = run_backtest(bars([10, 11, 12, 13]), spec(), SmaTrendStrategy(3))
    source = b"synthetic-source-v1"
    path = save_result(result, tmp_path / "result.json", source_data=source)
    payload = json.loads(path.read_text())
    assert payload["metadata"]["schema_version"] == SCHEMA_VERSION
    assert payload["metadata"]["package_version"] == __version__
    assert payload["metadata"]["strategy_implementation"] == "quant_intelligence.strategies.SmaTrendStrategy"
    assert payload["metadata"]["source_data_sha256"] == hashlib.sha256(source).hexdigest()
    assert payload["metadata"]["benchmark"] == "buy_and_hold"
    assert payload["metadata"]["experiment_id"] is None
    assert payload["metadata"]["created_at"]
    loaded = load_result(path)
    assert asdict(loaded) == asdict(result)
