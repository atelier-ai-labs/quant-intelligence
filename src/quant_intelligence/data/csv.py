import csv
from datetime import date
from pathlib import Path
from quant_intelligence.models import Bar
from .validation import validate_bars

def load_csv(path: str | Path) -> list[Bar]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        bars = [Bar(date.fromisoformat(row["date"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])) for row in csv.DictReader(handle)]
    return validate_bars(bars)
