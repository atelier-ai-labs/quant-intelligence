## Event-study go/no-go gate (research, v0.9 — expanded sample)

**Stacks on PR #1** (`eng/edgar-ollama-signals`) **and PR #2** (`eng/event-study-backtest`). This PR grows the event sample without redesigning the Ollama signal model: multi-year consecutive 10-K Item 1A diffs, optional 10-Q Item 1A YoY/QoQ/vs-10-K diffs, and a larger small/mid-cap ticker universe. Replay cache is preferred; Ollama is called only for NEW filing pairs.

```
expanded universe + multi-year/10-Q diffs ---> RiskSignal events (replay-first)
        |
        +-- Yahoo Finance daily closes (cached under data/prices/, gitignored)
        |     entry = as-of session close if as_of >= 16:00 ET; else next session close
        v
signed signal returns vs buy-and-hold vs random (N=500, seed=42)
        v
markdown + JSON report ---> GO / CONDITIONAL_GO / NO-GO
```

```bash
pip install -e ".[dev]"
# Default: replay prior LLM outputs — no Ollama
make event-study
ALLOW_NETWORK=1 make event-study   # refresh missing Yahoo prices

# Expanded live path (EDGAR + replay cache + Ollama for NEW only):
make event-study-expand
python -m quant_intelligence.signals.run --universe --all-events
```

**Entry rule (unchanged from PR #2):** convert `as_of` to America/New_York. If >= 16:00 ET on a trading day, entry = that session's close; else first subsequent session close. Horizons: 1 / 5 / 21 sessions. Bullish = long return; bearish = negated; neutral / low-confidence excluded from actionable cohort.

**10-Q pairing (documented):** prefer YoY prior 10-Q (~365d), else nearest prior 10-Q with extractable Item 1A, else nearest prior 10-K. Missing/short Item 1A fails closed for that event only.

**Gate criteria (unchanged):** GO only if actionable cohort beats **both** B&H and random on next-day or next-week by >=50 bps mean with **n >= 8**. Do **not** claim edge from long-only coincidence with B&H. Default NO-GO when uncertain.

### Latest expanded real run (2026-09-29 ET)

- **Universe:** 41 tickers (small/mid-cap heavy); INTC skipped (Item 1A extraction fail-closed on recent 10-Ks).
- **Signals:** 41 replay rows after expand (6 replayed from prior log, **35 new Ollama calls**, 0 Ollama hard failures). Actionable cohort **n=11** (bullish/bearish @ conf>=0.6).
- **Prices:** Yahoo Finance -> `data/prices/*.csv` cache.
- **Gate: NO-GO** — n=11 clears the sample floor, but signal mean is **below** both B&H and random on next_day and next_week. Bearish legs are especially weak; bullish mean ~ B&H on next_day (long-only coincidence, **not** edge).

| Horizon | Cohort | N | Hit rate | Mean return | Excess vs B&H | Excess vs random |
|---|---|---:|---:|---:|---:|---:|
| next_day | signal | 11 | 45.5% | -5.29% | -11.84% | -5.07% |
| next_day | buy-and-hold | 11 | 63.6% | 6.55% | — | — |
| next_day | random (500) | 500 | ~49% | -0.22% | — | — |
| next_week | signal | 11 | 36.4% | -9.54% | -20.20% | -9.13% |
| next_week | buy-and-hold | 11 | 54.5% | 10.67% | — | — |
| next_week | random (500) | 500 | ~49% | -0.41% | — | — |
| next_month | signal | 11 | 27.3% | -14.45% | -21.18% | -14.24% |

See `docs/artifacts/event_study.md` for the full table including bullish/bearish and confidence buckets.
