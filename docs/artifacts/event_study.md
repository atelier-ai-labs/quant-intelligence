# Event study report

- mode: `live`
- price_source: `yahoo-finance-cache`
- gate: **NO-GO**

## Summary

| Horizon | Cohort | N | Hit rate | Mean return | Sharpe-ish | Excess vs B&H | Excess vs random |
|---|---|---:|---:|---:|---:|---:|---:|
| next_day | signal | 3 | 66.67% | -11.17% | -0.465 | -22.33% | -10.21% |
| next_day | buy-and-hold | 3 | 33.33% | 11.17% | 0.465 | 0.00% | 12.12% |
| next_day | random | 500 | 48.93% | -0.96% | n/a | -12.12% | 0.00% |
| next_day | signal:bearish | 3 | 66.67% | -11.17% | -0.465 | -22.33% | -10.21% |
| next_day | signal:conf[0.8,1.0] | 3 | 66.67% | -11.17% | -0.465 | -22.33% | -10.21% |
| next_week | signal | 3 | 66.67% | -21.87% | -0.452 | -43.73% | -19.92% |
| next_week | buy-and-hold | 3 | 33.33% | 21.87% | 0.452 | 0.00% | 23.81% |
| next_week | random | 500 | 48.93% | -1.95% | n/a | -23.81% | 0.00% |
| next_week | signal:bearish | 3 | 66.67% | -21.87% | -0.452 | -43.73% | -19.92% |
| next_week | signal:conf[0.8,1.0] | 3 | 66.67% | -21.87% | -0.452 | -43.73% | -19.92% |
| next_month | signal | 3 | 66.67% | -24.87% | -0.332 | -49.73% | -22.09% |
| next_month | buy-and-hold | 3 | 33.33% | 24.87% | 0.332 | 0.00% | 27.65% |
| next_month | random | 500 | 48.93% | -2.78% | n/a | -27.65% | 0.00% |
| next_month | signal:bearish | 3 | 66.67% | -24.87% | -0.332 | -49.73% | -22.09% |
| next_month | signal:conf[0.8,1.0] | 3 | 66.67% | -24.87% | -0.332 | -49.73% | -22.09% |

## Gate rationale

- next_day: only 3 actionable events (need >=8 for a full GO); sample too small to claim edge
- next_week: only 3 actionable events (need >=8 for a full GO); sample too small to claim edge

## Notes

- entry rule: as-of close if as_of >= 16:00 ET on a trading day; else next session close. horizons: {'next_day': 1, 'next_week': 5, 'next_month': 21}. random draws=500 seed=42.
