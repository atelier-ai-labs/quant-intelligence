"""Durable-risk domain rules and the final order authorization boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from quant_intelligence.portfolio import BasisPointCostModel
from .models import OrderIntent, PortfolioSnapshot, RiskDecision, SignalAction


class RiskMode(StrEnum):
    NORMAL = "NORMAL"
    DAILY_HALT = "DAILY_HALT"
    CRITICAL_HALT = "CRITICAL_HALT"


CONSTITUTIONAL_MAX_POSITION = Decimal("0.20")
CONSTITUTIONAL_MAX_GROSS = Decimal("1.00")
CONSTITUTIONAL_MAX_LEVERAGE = Decimal("1.00")


@dataclass(frozen=True)
class RiskConfig:
    max_position_allocation: float = 0.20
    max_gross_exposure: float = 1.00
    max_leverage: float = 1.00
    daily_loss_limit: float = 0.05
    critical_drawdown_limit: float = 0.30
    max_order_notional: float = 10_000.0

    def __post_init__(self) -> None:
        for name in ("max_position_allocation", "max_gross_exposure", "max_leverage", "daily_loss_limit", "critical_drawdown_limit"):
            value = Decimal(str(getattr(self, name)))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_order_notional <= 0:
            raise ValueError("max_order_notional must be positive")
        hard_limits = {
            "max_position_allocation": CONSTITUTIONAL_MAX_POSITION,
            "max_gross_exposure": CONSTITUTIONAL_MAX_GROSS,
            "max_leverage": CONSTITUTIONAL_MAX_LEVERAGE,
        }
        for name, maximum in hard_limits.items():
            if Decimal(str(getattr(self, name))) > maximum:
                raise ValueError(f"{name} cannot exceed constitutional maximum {maximum}")
        for name in ("daily_loss_limit", "critical_drawdown_limit"):
            if Decimal(str(getattr(self, name))) > 1:
                raise ValueError(f"{name} must be at most 1")


@dataclass(frozen=True)
class RiskState:
    account_key: str = "default"
    session_id: str | None = None
    daily_baseline_equity: Decimal | None = None
    high_water_equity: Decimal | None = None
    mode: RiskMode = RiskMode.NORMAL
    daily_latched: bool = False
    critical_latched: bool = False
    last_evaluated_at: datetime | None = None
    reset_reason: str | None = None
    reset_at: datetime | None = None
    transition_sequence: int = 0
    reset_operator: str | None = None


@dataclass(frozen=True)
class RiskEvaluation:
    state: RiskState
    reason: str
    event_type: str | None = None


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("risk input is not numeric") from exc
    if not result.is_finite():
        raise ValueError("risk input is not finite")
    return result


class RiskStateService:
    def __init__(self, config: RiskConfig = RiskConfig()):
        self.config = config

    def observe(self, state: RiskState | None, *, equity: Any, session_id: str, timestamp: datetime, reconciled: bool = False, valid_session: bool = False) -> RiskEvaluation:
        equity_d = _money(equity)
        if equity_d <= 0 or not reconciled or not valid_session or not session_id.strip():
            raise ValueError("trustworthy positive equity, reconciliation, and a valid session are required")
        prior = state or RiskState()
        if prior.high_water_equity is None:
            next_state = RiskState(prior.account_key, session_id, equity_d, equity_d, prior.mode, prior.daily_latched, prior.critical_latched, timestamp, prior.reset_reason, prior.reset_at, prior.transition_sequence + 1, prior.reset_operator)
            return RiskEvaluation(next_state, "initialized from first trustworthy equity", "INITIALIZED")
        high_water = max(prior.high_water_equity, equity_d)
        baseline = prior.daily_baseline_equity
        daily_latched = prior.daily_latched
        mode = prior.mode
        event = None
        if session_id != prior.session_id:
            baseline = equity_d
            daily_latched = False
            if not prior.critical_latched:
                mode = RiskMode.NORMAL
            event = "NEW_SESSION"
        daily_loss = (baseline - equity_d) / baseline if baseline and baseline > 0 else Decimal(0)
        drawdown = (high_water - equity_d) / high_water if high_water > 0 else Decimal(0)
        if drawdown >= _money(self.config.critical_drawdown_limit):
            if not prior.critical_latched:
                event = "CRITICAL_HALT"
            mode = RiskMode.CRITICAL_HALT
        elif daily_loss >= _money(self.config.daily_loss_limit):
            if not prior.daily_latched:
                event = event or "DAILY_HALT"
            daily_latched = True; mode = RiskMode.DAILY_HALT
        if prior.critical_latched:
            mode = RiskMode.CRITICAL_HALT
        sequence = prior.transition_sequence + (1 if event else 0)
        next_state = RiskState(prior.account_key, session_id, baseline, high_water, mode, daily_latched, mode == RiskMode.CRITICAL_HALT or prior.critical_latched, timestamp, prior.reset_reason, prior.reset_at, sequence, prior.reset_operator)
        return RiskEvaluation(next_state, mode.value.lower(), event)

    def reset_critical(self, state: RiskState, *, equity: Any, session_id: str, timestamp: datetime, operator: str, reason: str) -> RiskEvaluation:
        if state.mode != RiskMode.CRITICAL_HALT or not operator.strip() or not reason.strip() or not session_id.strip():
            raise ValueError("critical halt reset requires an active halt, operator, reason, and valid session")
        value = _money(equity)
        if value <= 0:
            raise ValueError("trustworthy positive equity is required")
        next_state = RiskState(state.account_key, session_id, value, value, RiskMode.NORMAL, False, False, timestamp, reason.strip(), timestamp, state.transition_sequence + 1, operator.strip())
        return RiskEvaluation(next_state, "critical halt reset", "CRITICAL_HALT_RESET")


class RiskGate:
    def __init__(self, config: RiskConfig = RiskConfig(), transaction_cost_bps: float = 5.0, *, risk_mode: RiskMode | str = RiskMode.NORMAL):
        self.config = config
        self.cost_model = BasisPointCostModel(transaction_cost_bps)
        self.risk_mode = RiskMode(risk_mode)

    def evaluate(self, intent: OrderIntent, snapshot: PortfolioSnapshot, price: float) -> RiskDecision:
        def reject(reason: str, event_type: str) -> RiskDecision:
            return RiskDecision(False, reason, intent, self.risk_mode.value, event_type)

        if not isinstance(intent.quantity, int) or isinstance(intent.quantity, bool): return reject("quantity must be a whole number", "INVALID_ORDER_REJECTED")
        if intent.quantity <= 0: return reject("quantity must be positive", "INVALID_ORDER_REJECTED")
        if intent.order_type != "MARKET" or intent.asset_type != "EQUITY": return reject("unsupported order or asset type", "UNSUPPORTED_ORDER_REJECTED")
        if price <= 0: return reject("price must be positive", "INVALID_PRICE_REJECTED")
        p = _money(price); notional = _money(intent.quantity) * p
        if notional > _money(self.config.max_order_notional): return reject("order exceeds maximum notional", "ORDER_NOTIONAL_LIMIT_REJECTED")
        if not snapshot.positions_complete: return reject("reconciled position ownership is unavailable", "POSITION_STATE_UNAVAILABLE")
        current = next((position for position in snapshot.positions if position.symbol == intent.symbol), None)
        owned = current.shares if current else 0
        if intent.side == SignalAction.SELL:
            if intent.quantity > owned: return reject("cannot sell more shares than owned", "OVERSOLD_ORDER_REJECTED")
            return RiskDecision(True, "approved reduction", intent, self.risk_mode.value)
        if intent.side != SignalAction.BUY: return reject("unsupported signal for order", "UNSUPPORTED_SIGNAL_REJECTED")
        if self.risk_mode != RiskMode.NORMAL: return reject(f"risk mode {self.risk_mode.value} blocks increases", "RISK_HALT_REJECTED")
        if snapshot.equity <= 0 or not snapshot.valuation_complete: return reject("risk valuation is unavailable or incomplete", "VALUATION_UNAVAILABLE_REJECTED")
        equity = _money(snapshot.equity)
        total_cost = notional + _money(self.cost_model.cost(float(notional)))
        if total_cost > _money(snapshot.cash): return reject("order exceeds available cash", "INSUFFICIENT_CASH_REJECTED")
        resulting_position = _money(owned) * p + notional
        if resulting_position > equity * _money(self.config.max_position_allocation): return reject("resulting position exceeds maximum allocation", "POSITION_LIMIT_REJECTED")
        current_gross = _money(snapshot.asset_value)
        gross = current_gross + notional
        if gross > equity * _money(self.config.max_gross_exposure): return reject("resulting gross exposure exceeds maximum", "GROSS_EXPOSURE_LIMIT_REJECTED")
        if gross / equity > _money(self.config.max_leverage): return reject("resulting leverage exceeds maximum", "LEVERAGE_LIMIT_REJECTED")
        return RiskDecision(True, "approved", intent, self.risk_mode.value)
