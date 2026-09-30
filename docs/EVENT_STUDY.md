## Event-study go/no-go gate (research, v0.10 — signal redesign v2)

**Stacks on PR #1** (`eng/edgar-ollama-signals`), **PR #2** (`eng/event-study-backtest`), **and PR #3** (`eng/expand-event-sample`).

After PR #3’s **NO-GO** (n=11, signal underperformed B&H and random), Nate greenlit **one** signal redesign. This PR implements `PROMPT_VERSION = risk-diff-v2` hardening, then re-runs the event study once on the expanded ticker universe (latest 10-K pair per ticker). If still NO-GO, stop iterating (full case study is a later PR).

Portfolio goal remains honest AI evals + safety, not returns chasing.

### Hardening rules (enforced in code)

Documented in `quant_intelligence.signals.hardening` and the v2 system prompt:

1. **Citations = real YoY changes.** Snippet must appear in the Item 1A diff’s added/removed/changed sets for that as_of, must appear in the prompt text shown for that accession, and must **not** appear in any unchanged paragraph (boilerplate present in both years). Form 4 accessions are not valid citation targets. Bad citations → `no_signal`.
2. **Bullish requires insider cluster.** ≥2 open-market purchases (code `P`, acquired `A`) by **different** insiders with `accepted_at` in `[as_of − 90d, as_of]`. Otherwise bullish is capped to neutral (confidence ≤ 0.4). Bearish from risk-text worsening is allowed with a higher bar: confidence ≥ 0.7 **or** ≥1 citation grounded in an ADDED paragraph; else capped to neutral.
3. **Thin evidence → low conf / neutral.** If `(added+removed+changed) < 2` or `diff.similarity ≥ 0.97`, force neutral with confidence ≤ 0.35.
4. **Fail closed** on invalid JSON / schema / Ollama down / replay-log write failure (unchanged).
5. **Prompt version id** `risk-diff-v2` is written into every replay JSONL row; `find_cached_signal` matches on it so v1 caches are never reused for v2.

```bash
pip install -e ".[dev]"
export SEC_USER_AGENT="Atelier AI Labs research you@example.com"
ollama serve & ollama pull llama3.2

make event-study              # replay-only study
make event-study-redesign     # force Ollama risk-diff-v2, latest pair / ticker
pytest -q
```

**Entry rule (unchanged):** convert `as_of` to America/New_York. If >= 16:00 ET on a trading day, entry = that session's close; else first subsequent session close. Horizons: 1 / 5 / 21 sessions. Bullish = long return; bearish = negated; neutral / low-confidence excluded from actionable cohort.

**Gate criteria (unchanged):** GO only if actionable cohort beats **both** B&H and random on next-day or next-week by >=50 bps mean with **n >= 8**. Do **not** claim edge from long-only coincidence with B&H. Default NO-GO when uncertain.

### Real redesign run (2026-09-29 ET)

- **Prompt version:** `risk-diff-v2`
- **Universe:** 41 tickers; INTC skipped (Item 1A TOC-only); JPM skipped (only one 10-K visible)
- **Signals:** 39 latest-pair events after force-Ollama v2; **~39 new Ollama v2 calls** for the gate sample (plus a few aborted multi-year pairs from an earlier all-events attempt; total v2 JSONL rows ≈ 44)
- **Actionable n=3:** APPS, AI, JOBY — all **bearish** @ conf 0.8
- **Prices:** Yahoo Finance → `data/prices/` cache
- **Baselines:** B&H + random (500 draws, seed=42)
- **Gate: NO-GO** — n=3 below the ≥8 floor; signal mean also below B&H and random on next_day / next_week. Default NO-GO when sample too small / no clear edge. **Stop iterating** after this one redesign (case study = later PR).

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


### Sample actionable tickers (v2)

| Ticker | Direction | Conf | Notes |
|---|---|---:|---|
| APPS | bearish | 0.8 | ADDED transformation / unintended-consequence risks |
| AI | bearish | 0.8 | ADDED agentic-AI regulatory / privacy risks |
| JOBY | bearish | 0.8 | ADDED trade-policy / tariff risks |

Bullish was never actionable in this run (insider-cluster gate + model mostly returning neutral). Citation fail-closed caught Form-4 and ungrounded snippets (CAT, SOFI, UPWK, and others).
