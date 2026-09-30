.PHONY: test event-study event-study-live event-study-expand event-study-redesign

test:
	python -m pytest -q

# Default: replay fixtures / local replay log, no Ollama, no network unless prices missing + ALLOW_NETWORK=1
event-study:
	python -m quant_intelligence.event_study.run --replay data/signals/replay.jsonl \
		--price-cache data/prices \
		$(if $(ALLOW_NETWORK),--allow-network,) \
		--output-dir data/event_study \
		--artifact docs/artifacts

# Live latest-pair only (legacy PR #2 behaviour)
event-study-live:
	python -m quant_intelligence.event_study.run --live --universe --latest-only \
		--price-cache data/prices --allow-network \
		--replay-log data/signals/replay.jsonl \
		--output-dir data/event_study

# Expanded sample: multi-year 10-K pairs + 10-Q Item 1A diffs; replay cache first, Ollama for NEW only
event-study-expand:
	python -m quant_intelligence.event_study.run --live --universe --all-events \
		--price-cache data/prices --allow-network \
		--replay-log data/signals/replay.jsonl \
		--output-dir data/event_study \
		--artifact docs/artifacts

# Signal redesign v2: force Ollama with risk-diff-v2 on expanded ticker universe.
# Uses --latest-only (one 10-K pair / ticker) to match the PR #3 event-set budget (~60-85s/signal).
event-study-redesign:
	python -m quant_intelligence.event_study.run --live --universe --latest-only --force-ollama \
		--price-cache data/prices --allow-network \
		--replay-log data/signals/replay.jsonl \
		--output-dir data/event_study \
		--artifact docs/artifacts \
		-v
