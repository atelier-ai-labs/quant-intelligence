# Event study report

- mode: `replay`
- price_source: `yahoo-finance-cache`
- gate: **NO-GO**

## Summary

| Horizon | Cohort | N | Hit rate | Mean return | Sharpe-ish | Excess vs B&H | Excess vs random |
|---|---|---:|---:|---:|---:|---:|---:|
| next_day | signal | 11 | 45.45% | -5.29% | -0.322 | -11.84% | -5.07% |
| next_day | buy-and-hold | 11 | 63.64% | 6.55% | 0.411 | 0.00% | 6.78% |
| next_day | random | 500 | 49.24% | -0.22% | n/a | -6.78% | 0.00% |
| next_day | signal:bullish | 7 | 57.14% | 0.99% | 0.080 | 0.00% | 1.21% |
| next_day | signal:bearish | 4 | 25.00% | -16.28% | -0.876 | -32.56% | -16.06% |
| next_day | signal:conf[0.6,0.8) | 7 | 57.14% | 0.48% | 0.040 | 0.36% | 0.71% |
| next_day | signal:conf[0.8,1.0] | 4 | 25.00% | -15.39% | -0.779 | -33.20% | -15.17% |
| next_week | signal | 11 | 36.36% | -9.54% | -0.348 | -20.20% | -9.13% |
| next_week | buy-and-hold | 11 | 54.55% | 10.67% | 0.396 | 0.00% | 11.07% |
| next_week | random | 500 | 49.13% | -0.41% | n/a | -11.07% | 0.00% |
| next_week | signal:bullish | 7 | 42.86% | 0.89% | 0.065 | 0.00% | 1.29% |
| next_week | signal:bearish | 4 | 25.00% | -27.78% | -0.735 | -55.56% | -27.37% |
| next_week | signal:conf[0.6,0.8) | 7 | 42.86% | 0.63% | 0.046 | 1.33% | 1.03% |
| next_week | signal:conf[0.8,1.0] | 4 | 25.00% | -27.32% | -0.713 | -57.88% | -26.91% |
| next_month | signal | 11 | 27.27% | -14.45% | -0.404 | -21.18% | -14.24% |
| next_month | buy-and-hold | 11 | 45.45% | 6.72% | 0.176 | 0.00% | 6.94% |
| next_month | random | 500 | 49.78% | -0.21% | n/a | -6.94% | 0.00% |
| next_month | signal:bullish | 7 | 28.57% | -6.07% | -0.364 | 0.00% | -5.86% |
| next_month | signal:bearish | 4 | 25.00% | -29.12% | -0.510 | -58.23% | -28.90% |
| next_month | signal:conf[0.6,0.8) | 7 | 42.86% | -2.41% | -0.124 | 5.80% | -2.20% |
| next_month | signal:conf[0.8,1.0] | 4 | 0.00% | -35.52% | -0.700 | -68.39% | -35.31% |

## Gate rationale

- next_day: signal mean -0.0529 vs B&H 0.0655 vs random -0.0022 (n=11) — no clear edge
- next_week: signal mean -0.0954 vs B&H 0.1067 vs random -0.0041 (n=11) — no clear edge

## Notes

- entry rule: as-of close if as_of >= 16:00 ET on a trading day; else next session close. horizons: {'next_day': 1, 'next_week': 5, 'next_month': 21}. random draws=500 seed=42.
