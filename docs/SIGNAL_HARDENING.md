# Signal hardening (`risk-diff-v2`)

One redesign after PR #3 NO-GO. Prompt version id: **`risk-diff-v2`** (replay caches never mix with v1).

## Rules (implemented in `quant_intelligence.signals.hardening`)

### 1. Citations must quote real YoY Item 1A changes

- Each citation accession must be the new or prior Item 1A filing shown in the prompt (Form 4 accessions are **not** valid citation targets).
- The snippet must appear in that accession's prompt text **and** in the diff's **added / removed / changed** paragraph sets for that `as_of`.
- Snippets that only appear in **unchanged** boilerplate (present in both years), or that appear in both a delta paragraph and an unchanged paragraph, are **rejected** (fail closed).

### 2. Bullish requires Form 4 insider-buy cluster

- Bullish is allowed only when there are **≥2 open-market purchases (Form 4 code `P`)** by **distinct insiders** accepted within **~90 days** before `as_of`.
- Otherwise bullish is **capped to neutral** (confidence ≤ `CAP_NEUTRAL_CONFIDENCE`, 0.4).
- Bearish needs confidence ≥ 0.7 **or** an ADDED-paragraph citation (higher bar for risk-text worsening).

### 3. Confidence calibration / thin evidence

- Sparse diffs (few delta paragraphs or similarity ≥ 0.97) → **neutral** with confidence ≤ `THIN_MAX_CONFIDENCE` (0.35).
- Fail closed on bad citations, invalid JSON, schema failure, Ollama down, or replay-log write failure (unchanged from v1).

### 4. Local Ollama only + replay

- Ollama stays local (`127.0.0.1`).
- Every call logs prompts + outputs to the replay JSONL with `prompt_version=risk-diff-v2`.
- Same `RiskGate` / `OrderIntent` path; no live trading / Robinhood.

## Event-study redesign run

Re-ran latest-pair expanded universe with `--force-ollama` (v1 replay is wrong prompt). Gate remains **NO-GO** (n=3 actionable). See `docs/EVENT_STUDY.md` and `docs/artifacts/event_study_summary.json`. Stop iterating; case study is a later PR.
