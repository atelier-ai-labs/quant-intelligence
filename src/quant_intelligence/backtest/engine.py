import logging
from dataclasses import dataclass
from quant_intelligence.data.validation import validate_bars
from quant_intelligence.metrics import calculate_metrics
from quant_intelligence.models import Bar, EquityPoint, PortfolioState, StrategySpec, Trade
from quant_intelligence.portfolio import BasisPointCostModel

logger = logging.getLogger(__name__)

@dataclass
class BacktestResult:
    specification: StrategySpec
    actual_start: str
    actual_end: str
    states: list[PortfolioState]
    trades: list[Trade]
    metrics: dict
    benchmark_metrics: dict
    benchmark_equity: list[EquityPoint]

def buy_and_hold(bars: list[Bar], initial_capital: float, cost_model: BasisPointCostModel) -> tuple[dict, list[EquityPoint]]:
    price = bars[0].open
    shares = int(initial_capital / (price * (1 + cost_model.bps / 10_000)))
    cost = cost_model.cost(shares * price)
    cash = initial_capital - shares * price - cost
    equity = [cash + shares * bar.close for bar in bars]
    return calculate_metrics(equity, initial_capital, cost, 1 if shares else 0, len(bars) if shares else 0), [EquityPoint(bar.date, value) for bar, value in zip(bars, equity)]

def run_backtest(bars: list[Bar], specification: StrategySpec, strategy) -> BacktestResult:
    bars = validate_bars(bars)
    expected_window = specification.signal_parameters.get("window")
    if specification.signal != "sma_trend" or not hasattr(strategy, "window") or strategy.window != expected_window:
        raise ValueError("runtime strategy does not match StrategySpec")
    selected = [b for b in bars if (specification.start is None or b.date >= specification.start) and (specification.end is None or b.date <= specification.end)]
    if not selected: raise ValueError("no bars in requested date range")
    cost_model = BasisPointCostModel(specification.transaction_cost_bps)
    cash, shares, paid, invested_days = specification.initial_capital, 0, 0.0, 0
    states, trades, equity_series = [], [], []
    for index, bar in enumerate(selected):
        history = selected[:index]  # explicitly excludes today's bar
        desired = strategy.desired_position(history)
        if desired == "LONG" and shares == 0:
            quantity = int(cash / (bar.open * (1 + cost_model.bps / 10_000)))
            if quantity:
                gross = quantity * bar.open; cost = cost_model.cost(gross)
                cash -= gross + cost; shares = quantity; paid += cost
                trade = Trade(bar.date, specification.symbol, "BUY", quantity, bar.open, gross, cost, cash, cash + shares * bar.close, "signal_LONG")
                trades.append(trade); logger.info("trade_executed", extra={"symbol": specification.symbol, "side": "BUY", "date": str(bar.date)})
        elif desired == "CASH" and shares > 0:
            quantity = shares; gross = quantity * bar.open; cost = cost_model.cost(gross)
            cash += gross - cost; shares = 0; paid += cost
            trades.append(Trade(bar.date, specification.symbol, "SELL", quantity, bar.open, gross, cost, cash, cash, "signal_CASH"))
        asset_value = shares * bar.close; equity = cash + asset_value
        if shares: invested_days += 1
        equity_series.append(equity)
        states.append(PortfolioState(bar.date, cash, shares, asset_value, equity, paid, asset_value / equity if equity else 0, desired))
    logger.info("experiment_completed", extra={"symbol": specification.symbol, "trades": len(trades)})
    metrics = calculate_metrics(equity_series, specification.initial_capital, paid, len(trades), invested_days)
    benchmark, benchmark_equity = buy_and_hold(selected, specification.initial_capital, cost_model)
    return BacktestResult(specification, str(selected[0].date), str(selected[-1].date), states, trades, metrics, benchmark, benchmark_equity)
