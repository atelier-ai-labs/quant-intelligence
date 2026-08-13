from collections.abc import Sequence
from quant_intelligence.models import Bar

class SmaTrendStrategy:
    def __init__(self, window: int = 200):
        if window < 1: raise ValueError("window must be positive")
        self.window = window

    def desired_position(self, history: Sequence[Bar]) -> str:
        """Use only history, which must contain bars strictly before execution."""
        if len(history) < self.window: return "CASH"
        closes = [bar.close for bar in history[-self.window:]]
        return "LONG" if closes[-1] > sum(closes) / self.window else "CASH"
