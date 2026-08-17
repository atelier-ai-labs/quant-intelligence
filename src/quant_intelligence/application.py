from datetime import date
from pathlib import Path
from typing import Any

from quant_intelligence.experiments.store import ExperimentStore
from quant_intelligence.models import StrategySpec

class ExperimentService:
    def __init__(self, store: ExperimentStore):
        self.store = store

    def create(self, *, symbol: str, strategy: str, parameters: dict[str, Any], start: date | None, end: date | None, initial_capital: float, transaction_cost_bps: float, benchmark: str, data_path: str) -> tuple[str, Any]:
        if strategy != "sma_trend": raise ValueError("unsupported strategy: only sma_trend is available")
        if benchmark != "buy_and_hold": raise ValueError("unsupported benchmark: only buy_and_hold is available")
        window = parameters.get("window")
        if not isinstance(window, int) or isinstance(window, bool) or window < 1: raise ValueError("parameters.window must be a positive integer")
        spec = StrategySpec("sma-trend", symbol, start, end, initial_capital, signal="sma_trend", signal_parameters={"window": window}, transaction_cost_bps=transaction_cost_bps, benchmark=benchmark)
        return self.store.create_and_run(spec, Path(data_path))
