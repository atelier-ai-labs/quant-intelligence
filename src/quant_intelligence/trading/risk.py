from dataclasses import dataclass
from quant_intelligence.portfolio import BasisPointCostModel
from .models import OrderIntent, PortfolioSnapshot, RiskDecision, SignalAction

@dataclass(frozen=True)
class RiskConfig:
    max_position_allocation: float = 1.0
    max_order_notional: float = 10_000.0

class RiskGate:
    def __init__(self, config: RiskConfig = RiskConfig(), transaction_cost_bps: float = 5.0):
        if not 0 < config.max_position_allocation <= 1: raise ValueError("max_position_allocation must be in (0, 1]")
        self.config = config
        self.cost_model = BasisPointCostModel(transaction_cost_bps)

    def evaluate(self, intent: OrderIntent, snapshot: PortfolioSnapshot, price: float) -> RiskDecision:
        if not isinstance(intent.quantity, int) or isinstance(intent.quantity, bool): return RiskDecision(False, "quantity must be a whole number", intent)
        if intent.quantity <= 0: return RiskDecision(False, "quantity must be positive", intent)
        if intent.order_type != "MARKET" or intent.asset_type != "EQUITY": return RiskDecision(False, "unsupported order or asset type", intent)
        if price <= 0: return RiskDecision(False, "price must be positive", intent)
        notional = intent.quantity * price
        if notional > self.config.max_order_notional + 1e-9: return RiskDecision(False, "order exceeds maximum notional", intent)
        position = next((p for p in snapshot.positions if p.symbol == intent.symbol), None)
        owned = position.shares if position else 0
        if intent.side == SignalAction.SELL and intent.quantity > owned: return RiskDecision(False, "cannot sell more shares than owned", intent)
        if intent.side == SignalAction.BUY:
            total = notional + self.cost_model.cost(notional)
            if total > snapshot.cash + 1e-9: return RiskDecision(False, "order exceeds available cash", intent)
            if notional > snapshot.equity * self.config.max_position_allocation + 1e-9: return RiskDecision(False, "position exceeds maximum allocation", intent)
        elif intent.side != SignalAction.SELL: return RiskDecision(False, "unsupported signal for order", intent)
        return RiskDecision(True, "approved", intent)
