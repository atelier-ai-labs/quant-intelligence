# Event study report

- mode: `replay`
- price_source: `yahoo-finance-cache`
- gate: **NO-GO**

## Summary

| Horizon | Cohort | N | Hit rate | Mean return | Sharpe-ish | Excess vs B&H | Excess vs random |
|---|---|---:|---:|---:|---:|---:|---:|
| next_day | signal | 2 | 100.00% | 15.37% | 1.386 | 0.00% | 15.67% |
| next_day | buy-and-hold | 2 | 100.00% | 15.37% | 1.386 | 0.00% | 15.67% |
| next_day | random | 500 | 48.60% | -0.30% | n/a | -15.67% | 0.00% |
| next_day | signal:bullish | 2 | 100.00% | 15.37% | 1.386 | 0.00% | 15.67% |
| next_day | signal:conf[0.6,0.8) | 2 | 100.00% | 15.37% | 1.386 | 0.00% | 15.67% |
| next_week | signal | 2 | 100.00% | 18.39% | 6.348 | 0.00% | 18.88% |
| next_week | buy-and-hold | 2 | 100.00% | 18.39% | 6.348 | 0.00% | 18.88% |
| next_week | random | 500 | 48.60% | -0.48% | n/a | -18.88% | 0.00% |
| next_week | signal:bullish | 2 | 100.00% | 18.39% | 6.348 | 0.00% | 18.88% |
| next_week | signal:conf[0.6,0.8) | 2 | 100.00% | 18.39% | 6.348 | 0.00% | 18.88% |
| next_month | signal | 2 | 100.00% | 15.23% | 1.117 | 0.00% | 15.50% |
| next_month | buy-and-hold | 2 | 100.00% | 15.23% | 1.117 | 0.00% | 15.50% |
| next_month | random | 500 | 48.60% | -0.27% | n/a | -15.50% | 0.00% |
| next_month | signal:bullish | 2 | 100.00% | 15.23% | 1.117 | 0.00% | 15.50% |
| next_month | signal:conf[0.6,0.8) | 2 | 100.00% | 15.23% | 1.117 | 0.00% | 15.50% |

## Gate rationale

- next_day: only 2 actionable events (need >=8 for a full GO); sample too small to claim edge
- next_week: only 2 actionable events (need >=8 for a full GO); sample too small to claim edge

## Notes

- entry rule: as-of close if as_of >= 16:00 ET on a trading day; else next session close. horizons: {'next_day': 1, 'next_week': 5, 'next_month': 21}. random draws=500 seed=42.
