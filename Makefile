.PHONY: test event-study event-study-live

test:
	python -m pytest -q

# Default: replay fixtures / local replay log, no Ollama, no network unless prices missing + ALLOW_NETWORK=1
event-study:
	python -m quant_intelligence.event_study.run --replay data/signals/replay.jsonl \
		--price-cache data/prices \
		$(if $(ALLOW_NETWORK),--allow-network,) \
		--output-dir data/event_study \
		--artifact docs/artifacts

event-study-live:
	python -m quant_intelligence.event_study.run --live --universe \
		--price-cache data/prices --allow-network \
		--replay-log data/signals/replay.jsonl \
		--output-dir data/event_study
