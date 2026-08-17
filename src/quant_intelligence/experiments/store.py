import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from quant_intelligence.backtest import BacktestResult, run_backtest
from quant_intelligence.data.csv import load_csv
from quant_intelligence.models import StrategySpec
from quant_intelligence.strategies import SmaTrendStrategy
from .persistence import load_result, save_result

class ExperimentNotFound(FileNotFoundError):
    pass

class ExperimentStore:
    def __init__(self, root: str | Path = "experiments"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, experiment_id: str) -> Path:
        return self.root / f"{experiment_id}.json"

    def get(self, experiment_id: str) -> BacktestResult:
        path = self.path_for(experiment_id)
        if not path.is_file(): raise ExperimentNotFound(experiment_id)
        return load_result(path)

    def get_payload(self, experiment_id: str) -> dict[str, Any]:
        path = self.path_for(experiment_id)
        if not path.is_file(): raise ExperimentNotFound(experiment_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                spec = payload["specification"]; metadata = payload.get("metadata", {}); metrics = payload["metrics"]
                summaries.append({"experiment_id": metadata.get("experiment_id") or path.stem, "symbol": spec["symbol"], "strategy": spec["name"], "parameters": spec.get("signal_parameters", {}), "requested_start": spec.get("start"), "requested_end": spec.get("end"), "actual_start": payload.get("actual_start"), "actual_end": payload.get("actual_end"), "initial_capital": spec["initial_capital"], "total_return": metrics.get("total_return"), "benchmark": spec.get("benchmark"), "created_at": metadata.get("created_at"), "package_version": metadata.get("package_version")})
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(summaries, key=lambda item: item.get("created_at") or "", reverse=True)

    def create_and_run(self, spec: StrategySpec, data_path: str | Path) -> tuple[str, BacktestResult]:
        experiment_id = uuid4().hex
        source = Path(data_path).read_bytes()
        result = run_backtest(load_csv(data_path), spec, SmaTrendStrategy(int(spec.signal_parameters["window"])))
        save_result(result, self.path_for(experiment_id), source_data=source, experiment_id=experiment_id, created_at=datetime.now(timezone.utc).isoformat())
        return experiment_id, result
