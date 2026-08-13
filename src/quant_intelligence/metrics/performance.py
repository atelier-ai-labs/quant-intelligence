from math import sqrt

def _returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i-1] - 1 for i in range(1, len(equity)) if equity[i-1] > 0]

def calculate_metrics(equity: list[float], initial_capital: float, costs: float = 0, trades: int = 0, invested_days: int = 0, risk_free_rate: float = 0.0) -> dict[str, float | int | None]:
    if not equity: raise ValueError("equity series cannot be empty")
    total_return = equity[-1] / initial_capital - 1
    days = len(equity) - 1
    years = days / 252 if days else 0
    cagr = (equity[-1] / initial_capital) ** (1 / years) - 1 if years > 0 and equity[-1] > 0 else None
    returns = _returns(equity)
    volatility = (sum((r - sum(returns)/len(returns)) ** 2 for r in returns) / (len(returns)-1)) ** 0.5 * sqrt(252) if len(returns) > 1 else None
    excess = [r - risk_free_rate / 252 for r in returns]
    mean = sum(excess) / len(excess) if excess else 0
    std = (sum((r - mean) ** 2 for r in excess) / (len(excess)-1)) ** 0.5 if len(excess) > 1 else 0
    sharpe = mean / std * sqrt(252) if std else None
    peak = equity[0]; max_drawdown = 0.0
    for value in equity:
        peak = max(peak, value); max_drawdown = min(max_drawdown, value / peak - 1)
    return {"total_return": total_return, "cagr": cagr, "annualized_volatility": volatility, "sharpe_ratio": sharpe, "maximum_drawdown": max_drawdown, "number_of_trades": trades, "transaction_costs_paid": costs, "percentage_time_invested": invested_days / len(equity) if equity else None, "risk_free_rate": risk_free_rate, "annualization_days": 252}
