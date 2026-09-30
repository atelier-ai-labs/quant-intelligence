## Event-study go/no-go gate (research, v0.8)

Stacks on the EDGAR/Ollama signal layer. This is an **honest eval**, not a returns claim: measure next-day / next-week / next-month returns after each signal with a strict point-in-time entry rule, compare to buy-and-hold and a seeded random-direction baseline, and decide GO vs NO-GO.

```
replay.jsonl (or live Ollama) ──> RiskSignal events
        │
        ├─ Yahoo Finance daily closes (cached under data/prices/, gitignored)
        │     entry = as-of session close if as_of ≥ 16:00 ET; else next session close
        │     never uses same-day prices that would not yet be known
        ▼
signed signal returns vs buy-and-hold vs random (N=500, seed=42)
        │
        ▼
markdown + JSON report ──> GO / CONDITIONAL_GO / NO-GO
```

Modules: `quant_intelligence.event_study` (prices, replay loader, baselines, metrics, study, CLI). Strategies stay intent-only; RiskGate still decides. No broker calls. Human approval / daily loss limit / allowlist remain out of scope for this PR.

```bash
# Default: replay prior LLM outputs — no Ollama, uses local price cache
make event-study
# or:
python -m quant_intelligence.event_study.run --replay data/signals/replay.jsonl --price-cache data/prices

# Refresh missing prices from Yahoo (free):
ALLOW_NETWORK=1 make event-study
python -m quant_intelligence.event_study.run --replay ... --allow-network

# Live mode (calls Ollama, then studies):
python -m quant_intelligence.event_study.run --live --universe --allow-network
```

CI/tests default to fixture prices and a mocked-free replay path (`tests/unit/test_event_study.py`). A sample report from the real replay run is committed under `docs/artifacts/`.

**Entry rule (documented and applied consistently):** convert `as_of` to America/New_York. If the timestamp is on/after 16:00 ET and that calendar date is a trading day in the price series, entry price is that day's close. Otherwise entry is the first subsequent session's close. Horizons are 1 / 5 / 21 trading sessions after entry. Bullish uses the long return; bearish uses the negated return; neutral / `no_signal` / low-confidence rows are excluded from the actionable cohort (still listed in the event table).

**Gate criteria:** GO only if the actionable cohort beats **both** buy-and-hold and random on a primary window (next-day or next-week) by a clear absolute margin (≥50 bps mean) with enough events (default ≥8). Otherwise NO-GO (default when uncertain). A weaker CONDITIONAL_GO is reserved for a specific confidence/direction bucket that clearly beats baselines with a usable subsample.

**Latest real replay run (Yahoo cache, no live Ollama):** 6 logged signals, 2 actionable (PLUG bullish@0.6, OPEN bullish@0.7). Signal mean equals buy-and-hold on every horizon (both events were long) and beats random, but **n=2 → NO-GO**. Direction quality remains unreliable (PLUG's bullish label contradicts its bearish rationale). See `docs/artifacts/event_study.md`.
