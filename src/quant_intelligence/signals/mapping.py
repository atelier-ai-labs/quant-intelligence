"""Map validated signals to OrderIntent and route them through RiskGate. This module never touches a broker."""

from __future__ import annotations

from dataclasses import dataclass

from quant_intelligence.trading.models import OrderIntent, PortfolioSnapshot, RiskDecision, SignalAction
from quant_intelligence.trading.risk import RiskGate

from .schema import RiskSignal

DEFAULT_MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class SignalRouting:
    signal: RiskSignal
    intent: OrderIntent | None
    decision: RiskDecision


def signal_to_intent(signal: RiskSignal, *, quantity: int = 1, owned_shares: int = 0, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> tuple[OrderIntent | None, str]:
    """bullish -> BUY; bearish -> SELL only shares already owned (no shorting); anything else -> no intent."""
    if signal.status != "ok": return None, f"no intent: {signal.reason or 'no signal'}"
    if signal.direction == "neutral": return None, "no intent: neutral signal"
    if signal.confidence < min_confidence: return None, f"no intent: confidence {signal.confidence:.2f} below {min_confidence:.2f}"
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0: return None, "no intent: quantity must be a positive whole number"
    accessions = ",".join(sorted({c.accession_no for c in signal.citations}))
    reason = f"edgar-risk-diff {signal.direction} conf={signal.confidence:.2f} as_of={signal.as_of.isoformat()} model={signal.model} cites={accessions}"
    if signal.direction == "bullish": return OrderIntent(signal.ticker, SignalAction.BUY, quantity, reason=reason), "intent: buy"
    if owned_shares <= 0: return None, "no intent: bearish signal but no position to reduce (no shorting)"
    return OrderIntent(signal.ticker, SignalAction.SELL, min(quantity, owned_shares), reason=reason), "intent: sell"


def route_signal(signal: RiskSignal, risk_gate: RiskGate, snapshot: PortfolioSnapshot, price: float | None, *, quantity: int = 1,
                 min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> SignalRouting:
    """Signal -> OrderIntent -> RiskGate.evaluate. Returns the decision; submission stays with the execution layer."""
    if signal.as_of > snapshot.timestamp:
        return SignalRouting(signal, None, RiskDecision(False, "signal as_of is after the portfolio snapshot (look-ahead)", None))
    owned = next((p.shares for p in snapshot.positions if p.symbol == signal.ticker), 0)
    intent, why = signal_to_intent(signal, quantity=quantity, owned_shares=owned, min_confidence=min_confidence)
    if intent is None: return SignalRouting(signal, None, RiskDecision(False, why, None))
    if price is None or price <= 0: return SignalRouting(signal, intent, RiskDecision(False, "no reference price supplied; risk gate not satisfied", intent))
    return SignalRouting(signal, intent, risk_gate.evaluate(intent, snapshot, price))
