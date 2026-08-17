import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from .models import Position, TradingDecision

@dataclass
class OperationalStatus:
    state: str = "stopped"
    last_cycle_id: str | None = None
    last_cycle_timestamp: datetime | None = None
    last_cycle_outcome: str | None = None
    last_error: str | None = None
    current_equity: float | None = None
    current_cash: float | None = None
    current_positions: tuple[Position, ...] = field(default_factory=tuple)
    most_recent_market_data_timestamp: datetime | None = None

    def update_from_decision(self, decision: TradingDecision) -> None:
        self.last_cycle_id = decision.cycle_id; self.last_cycle_timestamp = decision.timestamp; self.last_cycle_outcome = decision.outcome; self.last_error = decision.error
        self.most_recent_market_data_timestamp = decision.data_timestamp
        snapshot = decision.portfolio_after or decision.portfolio_before
        if snapshot:
            self.current_equity = snapshot.equity; self.current_cash = snapshot.cash; self.current_positions = snapshot.positions

class StatusStore:
    def __init__(self, path: str | Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> OperationalStatus:
        if not self.path.is_file(): return OperationalStatus()
        data = json.loads(self.path.read_text(encoding="utf-8")); positions = tuple(Position(item["symbol"], item["shares"], item["average_price"]) for item in data.get("current_positions", []))
        return OperationalStatus(data.get("state", "stopped"), data.get("last_cycle_id"), datetime.fromisoformat(data["last_cycle_timestamp"]) if data.get("last_cycle_timestamp") else None, data.get("last_cycle_outcome"), data.get("last_error"), data.get("current_equity"), data.get("current_cash"), positions, datetime.fromisoformat(data["most_recent_market_data_timestamp"]) if data.get("most_recent_market_data_timestamp") else None)

    def save(self, status: OperationalStatus) -> None:
        data = asdict(status)
        for key in ("last_cycle_timestamp", "most_recent_market_data_timestamp"):
            if data[key] is not None: data[key] = data[key].isoformat()
        self.path.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
