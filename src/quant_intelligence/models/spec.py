from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

@dataclass(frozen=True)
class StrategySpec:
    name: str
    symbol: str
    start: date | None
    end: date | None
    initial_capital: float
    signal: str = "sma_trend"
    signal_parameters: dict[str, Any] = field(default_factory=lambda: {"window": 200})
    execution_timing: str = "next_open"
    position_sizing: str = "all_cash_no_fractional_shares"
    transaction_cost_bps: float = 5.0
    benchmark: str = "buy_and_hold"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_capital <= 0: raise ValueError("initial_capital must be positive")
        if self.transaction_cost_bps < 0: raise ValueError("transaction_cost_bps cannot be negative")
        if self.execution_timing != "next_open": raise ValueError("Phase 1 supports next_open only")
        if self.position_sizing != "all_cash_no_fractional_shares": raise ValueError("Phase 1 supports integer all-cash sizing only")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["start"] = self.start.isoformat() if self.start else None
        value["end"] = self.end.isoformat() if self.end else None
        return value
