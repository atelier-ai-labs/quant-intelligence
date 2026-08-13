# Phase 1 Methodology

## Timing

For each selected trading row, the strategy receives a history ending at the prior row. The SMA strategy is therefore unable to read today's open, high, low, close, or volume. A desired state change is executed at today's open.

## Accounting

Cash is reduced by buy notional plus cost and increased by sell proceeds less cost. Shares are integers. At every close, equity equals cash plus shares times that day's close. No forward filling is performed.

## Costs and benchmark

The cost model charges a configurable number of basis points on gross notional. It is deliberately simple and is not a claim of realistic execution. Buy-and-hold is calculated separately: whole shares are purchased at the first selected open, the same cost assumption applies, residual cash remains uninvested, and the position is valued at later closes.

## Metrics

- Total return: ending equity / initial capital − 1.
- CAGR: `(ending / starting)^(1 / years) − 1`, with years equal to observations / 252.
- Annualized volatility: sample standard deviation of close-to-close equity returns × √252.
- Sharpe: mean daily excess return / daily sample standard deviation × √252; annual risk-free rate defaults to 0%.
- Maximum drawdown: the minimum of equity / running peak − 1.
- Time invested: observations with positive asset value / total observations.

Insufficient observations return `null` where a metric would be misleading rather than fabricating a value.

## Biases and limitations

This foundation prevents the specified look-ahead error, but it cannot eliminate survivorship bias, data-snooping, stale or erroneous vendor data, corporate-action ambiguity, market impact, bid/ask spreads, partial fills, taxes, liquidity constraints, or regime change. Daily open execution is an idealized convention. The data provider must state whether prices are adjusted for splits and dividends.
