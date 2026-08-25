import hashlib
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from quant_intelligence.portfolio import BasisPointCostModel
from quant_intelligence.strategies import SmaTrendStrategy
from .audit import TradingAuditStore
from .broker import Broker, BrokerError, ExecutionDisabled, BrokerSubmissionUnknown, BrokerUnavailable
from .clock import Clock, SystemClock
from .market import MarketDataProvider, MarketDataUnavailable
from .models import OrderIntent, PortfolioSnapshot, RiskDecision, SignalAction, TradingDecision
from .risk import RiskConfig, RiskGate, RiskMode

logger = logging.getLogger(__name__)

class TradingCycleService:
    def __init__(self, *, strategy: SmaTrendStrategy, broker: Broker, market_data: MarketDataProvider, risk_gate: RiskGate, audit_store: TradingAuditStore, strategy_name: str = "sma_trend", strategy_version: str = "1", clock: Clock | None = None, execution_mode: str = "paper", max_execution_shares: int | None = None, risk_state_store: Any | None = None, risk_config: RiskConfig | None = None, risk_account_key: str = "default"):
        if max_execution_shares is not None and max_execution_shares < 1:
            raise ValueError("max_execution_shares must be positive")
        self.strategy = strategy; self.broker = broker; self.market_data = market_data; self.risk_gate = risk_gate; self.audit_store = audit_store; self.strategy_name = strategy_name; self.strategy_version = strategy_version; self.clock = clock or SystemClock(); self.execution_mode = execution_mode; self.max_execution_shares = max_execution_shares; self.risk_state_store = risk_state_store; self.risk_config = risk_config or risk_gate.config; self.risk_account_key = risk_account_key

    def _cycle_id(self, symbol: str, data_timestamp: datetime, market_session: str | None = None) -> str:
        raw = json.dumps({"strategy": self.strategy_name, "version": self.strategy_version, "symbol": symbol, "market_session": market_session or data_timestamp.date().isoformat(), "data_timestamp": data_timestamp.isoformat(), "window": self.strategy.window, "execution_mode": self.execution_mode}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _legacy_cycle_id(self, symbol: str, data_timestamp: datetime) -> str:
        raw = json.dumps({"strategy": self.strategy_name, "version": self.strategy_version, "symbol": symbol, "data_timestamp": data_timestamp.isoformat(), "window": self.strategy.window, "execution_mode": self.execution_mode}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _client_order_id(cycle_id: str) -> str:
        return f"qi-{cycle_id[:40]}"

    def _snapshot(self, symbol: str, price: float, timestamp: datetime) -> PortfolioSnapshot:
        return self.broker.get_portfolio_snapshot({symbol: price}, timestamp)

    def run(self, symbol: str, now: datetime | None = None, *, reconciled: bool = False, session_id: str | None = None, completed_session: Any | None = None, post_close_delay: timedelta = timedelta()) -> TradingDecision:
        timestamp = now or self.clock.now()
        requested_session = session_id or (completed_session.session_date.isoformat() if completed_session is not None else None)
        try:
            if completed_session is None:
                market = self.market_data.get_completed_bars(symbol, timestamp)
            else:
                market = self.market_data.get_completed_bars(symbol, timestamp, completed_session=completed_session, post_close_delay=post_close_delay)
        except (MarketDataUnavailable, ValueError) as exc:
            return self._save_no_trade(symbol, timestamp, "HOLD", f"market data unavailable: {exc}", str(exc), market_session=requested_session)
        try:
            before = self._snapshot(symbol, market.latest_price, timestamp)
        except Exception as exc:
            return self._save_no_trade(symbol, timestamp, "HOLD", f"broker state unavailable: {exc}", str(exc), market_session=requested_session)
        observed_session = market.data_timestamp.date().isoformat()
        requested_session = requested_session or observed_session
        cycle_id = self._cycle_id(symbol, market.data_timestamp, requested_session)
        if self.risk_state_store is not None:
            try:
                current_state = self.risk_state_store.load_risk_state(self.risk_account_key)
                if current_state is not None:
                    self.risk_gate.risk_mode = RiskMode(current_state.mode)
            except Exception as exc:
                return self._save_no_trade(symbol, timestamp, "HOLD", f"risk state unavailable: {exc}", str(exc), market_session=requested_session)
        if observed_session != requested_session:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, SignalAction.HOLD, "market data session does not match completed session", before, None, RiskDecision(False, "invalid completed market session", None, self.risk_gate.risk_mode.value), None, None, before, "NO_TRADE", "invalid completed market session", market_session=requested_session))
        existing = self.audit_store.get(cycle_id)
        if existing is None:
            legacy_id = self._legacy_cycle_id(symbol, market.data_timestamp)
            if legacy_id != cycle_id:
                legacy = self.audit_store.get(legacy_id)
                if legacy is not None and (legacy.market_session or (legacy.data_timestamp.date().isoformat() if legacy.data_timestamp else None)) == requested_session:
                    existing = legacy
        if existing is not None:
            logger.info("duplicate_cycle", extra={"cycle_id": cycle_id, "symbol": symbol})
            return existing
        if not self.market_data.is_fresh(market, timestamp):
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, SignalAction.HOLD, "market data is stale", before, None, RiskDecision(False, "stale market data", None, self.risk_gate.risk_mode.value), None, None, before, "NO_TRADE", "stale market data", market_session=requested_session))
        if self.risk_state_store is not None:
            try:
                evaluation = self.risk_state_store.evaluate_risk_state(equity=before.equity, session_id=observed_session, timestamp=timestamp, reconciled=reconciled, valid_session=True, account_key=self.risk_account_key, config=self.risk_config)
                self.risk_gate.risk_mode = RiskMode(evaluation.state.mode)
            except Exception as exc:
                return self._save_no_trade(symbol, timestamp, "HOLD", f"risk state unavailable: {exc}", str(exc), market_session=requested_session)
        desired = self.strategy.desired_position(market.bars)
        owned = next((position.shares for position in before.positions if position.symbol == symbol), 0)
        if desired == "LONG" and owned == 0:
            quantity = int(before.cash / (market.latest_price * (1 + self.risk_gate.cost_model.bps / 10_000)))
            signal = SignalAction.BUY; reason = "SMA desired position is LONG"
            intent = OrderIntent(symbol, signal, quantity, reason=reason)
        elif desired == "CASH" and owned > 0:
            managed = getattr(self.broker, "is_position_managed", lambda _symbol: True)(symbol)
            if not managed:
                signal = SignalAction.HOLD; reason = "unmanaged broker position preserved"; intent = None
            else:
                signal = SignalAction.SELL; reason = "SMA desired position is CASH"; intent = OrderIntent(symbol, signal, owned, reason=reason)
        else:
            signal = SignalAction.HOLD; reason = "desired position matches current portfolio"; intent = None
        if intent is None:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, None, RiskDecision(True, "no order required", None, self.risk_gate.risk_mode.value), None, None, before, "HOLD", market_session=requested_session))
        proposed_order = replace(intent, client_order_id=self._client_order_id(cycle_id), cycle_id=cycle_id)
        execution_intent = proposed_order
        if self.max_execution_shares is not None:
            execution_intent = replace(proposed_order, quantity=min(proposed_order.quantity, self.max_execution_shares), reason=f"{proposed_order.reason}; execution cap {self.max_execution_shares} share(s)")
        risk = self.risk_gate.evaluate(execution_intent, before, market.latest_price)
        if not risk.approved:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, risk, None, None, before, "NO_TRADE", None, execution_intent, market_session=requested_session))
        # Durable intent is written before an external submission. If this
        # transaction fails, no broker request is permitted to proceed.
        if self.execution_mode != "observation":
            self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, risk, None, None, before, "PENDING_SUBMISSION", None, execution_intent, market_session=requested_session))
        try:
            order, fill = self.broker.submit_order(execution_intent, market.latest_price, timestamp)
        except ExecutionDisabled as exc:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, risk, None, None, before, f"WOULD_{signal.value}", str(exc), execution_intent, market_session=requested_session))
        except BrokerSubmissionUnknown as exc:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, risk, None, None, before, "UNKNOWN", str(exc), execution_intent, market_session=requested_session))
        except (BrokerUnavailable, BrokerError) as exc:
            return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, RiskDecision(False, str(exc), execution_intent, self.risk_gate.risk_mode.value), None, None, before, "NO_TRADE", str(exc), execution_intent, market_session=requested_session))
        after = self._snapshot(symbol, market.latest_price, timestamp)
        outcome = "EXECUTED" if fill is not None else order.status
        return self._save_decision(TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, market.data_timestamp, signal, reason, before, proposed_order, risk, order, fill, after, outcome, None, execution_intent, market_session=requested_session))

    def _save_decision(self, decision: TradingDecision) -> TradingDecision:
        if decision.market_session is None:
            decision = replace(decision, market_session=decision.data_timestamp.date().isoformat() if decision.data_timestamp else None)
        if decision.execution_mode != self.execution_mode:
            decision = replace(decision, execution_mode=self.execution_mode)
        self.audit_store.save(decision)
        return decision

    def _save_no_trade(self, symbol: str, timestamp: datetime, signal: str, reason: str, error: str, *, market_session: str | None = None) -> TradingDecision:
        cycle_id = hashlib.sha256(f"{self.strategy_name}:{self.execution_mode}:{symbol}:{market_session or 'unavailable'}:{timestamp.isoformat()}".encode()).hexdigest()
        decision = TradingDecision(cycle_id, timestamp, symbol, self.strategy_name, {"window": self.strategy.window}, None, SignalAction(signal), reason, None, None, RiskDecision(False, reason, None, self.risk_gate.risk_mode.value), None, None, None, "NO_TRADE", error, market_session=market_session)
        return self._save_decision(decision)
