class BasisPointCostModel:
    """Simplifying cost assumption: bps applied to gross traded notional."""
    def __init__(self, bps: float):
        if bps < 0: raise ValueError("bps cannot be negative")
        self.bps = bps

    def cost(self, gross_notional: float) -> float:
        return gross_notional * self.bps / 10_000
