import json
from dataclasses import asdict
from pathlib import Path
from quant_intelligence.backtest import BacktestResult

def save_result(result: BacktestResult, path: str | Path) -> Path:
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    payload["specification"]["start"] = result.specification.start.isoformat() if result.specification.start else None
    payload["specification"]["end"] = result.specification.end.isoformat() if result.specification.end else None
    target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return target
