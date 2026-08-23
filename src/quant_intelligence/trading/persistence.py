"""Operational persistence contracts and SQLAlchemy implementation.

Research artifacts deliberately remain outside this module and continue to use
the existing JSON experiment store.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Float,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .audit import TradingAuditStore, decision_from_dict
from .models import (
    Fill,
    Order,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    SignalAction,
    TradingDecision,
)
from .status import OperationalStatus, StatusStore


class PersistenceConfigurationError(RuntimeError):
    """Raised when PostgreSQL persistence is explicitly enabled incorrectly."""


class OperationalRepository(Protocol):
    def get(self, cycle_id: str) -> TradingDecision | None: ...
    def list(self) -> list[TradingDecision]: ...
    def save(self, decision: TradingDecision) -> None: ...
    def load_status(self) -> OperationalStatus: ...
    def save_status(self, status: OperationalStatus) -> None: ...


class Base(DeclarativeBase):
    pass


class TradingCycleRow(Base):
    __tablename__ = "trading_cycles"
    __table_args__ = (UniqueConstraint("strategy_name", "strategy_version", "symbol", "market_session", "mode", name="uq_trading_cycle_identity"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_session: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    signal: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    strategy_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    signal_reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    risk_approved: Mapped[bool] = mapped_column(nullable=False, default=False)
    risk_reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    portfolio_before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    portfolio_after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    proposed_order: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_order: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reconciliation_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_orders_requested_positive"),
        CheckConstraint("submitted_quantity > 0", name="ck_orders_submitted_positive"),
        CheckConstraint("filled_quantity >= 0", name="ck_orders_filled_nonnegative"),
        CheckConstraint("filled_quantity <= submitted_quantity", name="ck_orders_filled_lte_submitted"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_supported_side"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(128), ForeignKey("trading_cycles.id", ondelete="RESTRICT"), nullable=False, index=True)
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FillRow(Base):
    __tablename__ = "fills"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_fills_quantity_positive"), CheckConstraint("price > 0", name="ck_fills_price_positive"))

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    broker_fill_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    transaction_cost: Mapped[float] = mapped_column(Float, nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationRow(Base):
    __tablename__ = "reconciliations"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cycle_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("trading_cycles.id", ondelete="RESTRICT"), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    action_taken: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ManagedPositionRow(Base):
    __tablename__ = "managed_positions"
    __table_args__ = (CheckConstraint("quantity >= 0", name="ck_managed_positions_quantity_nonnegative"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    broker_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationalStatusRow(Base):
    __tablename__ = "operational_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    service_state: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_state: Mapped[str] = mapped_column(String(32), nullable=False)
    halt_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    broker_connected: Mapped[bool | None] = mapped_column(nullable=True)
    market_data_healthy: Mapped[str] = mapped_column(String(32), nullable=False)
    last_cycle_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_cycle_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciliation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_completed_session: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unresolved_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_cycle_outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    current_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    most_recent_market_data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="paper")
    execution_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    broker_health: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    trading_health: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    managed_symbols: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _snapshot(value: PortfolioSnapshot | None) -> dict[str, Any] | None:
    return _json_value(asdict(value)) if value else None


def _intent(value: OrderIntent | None) -> dict[str, Any] | None:
    return _json_value(asdict(value)) if value else None


def _snapshot_from_json(value: dict[str, Any] | None) -> PortfolioSnapshot | None:
    if value is None:
        return None
    return PortfolioSnapshot(datetime.fromisoformat(value["timestamp"]), value["cash"], tuple(Position(item["symbol"], item["shares"], item["average_price"]) for item in value["positions"]), value["asset_value"], value["equity"], value["transaction_costs_paid"])


def _intent_from_json(value: dict[str, Any] | None) -> OrderIntent | None:
    if value is None:
        return None
    return OrderIntent(value["symbol"], SignalAction(value["side"]), value["quantity"], value.get("order_type", "MARKET"), value.get("asset_type", "EQUITY"), value.get("reason", ""), value.get("client_order_id"), value.get("cycle_id"))


def _decision_from_row(row: TradingCycleRow, order_row: OrderRow | None, fill_row: FillRow | None, reconciliation: dict[str, Any] | None = None) -> TradingDecision:
    order = None
    if order_row:
        intent = _intent_from_json(row.execution_order or row.proposed_order) or OrderIntent(order_row.symbol, SignalAction(order_row.side), order_row.submitted_quantity, client_order_id=order_row.client_order_id, cycle_id=row.id)
        order = Order(order_row.id, intent, order_row.submitted_at, order_row.status, order_row.client_order_id, order_row.broker_order_id, order_row.filled_quantity, order_row.average_fill_price, None)
    fill = None
    if fill_row:
        side = order.intent.side if order else SignalAction.BUY
        fill = Fill(fill_row.order_id, order_row.symbol if order_row else row.symbol, side, fill_row.quantity, fill_row.price, fill_row.quantity * fill_row.price, fill_row.transaction_cost, fill_row.filled_at)
    risk_intent = _intent_from_json(row.execution_order or row.proposed_order)
    risk = RiskDecision(row.risk_approved, row.risk_reason, risk_intent)
    return TradingDecision(row.id, row.created_at, row.symbol, row.strategy_name, row.strategy_parameters or {}, datetime.fromisoformat(row.market_session) if row.market_session else None, SignalAction(row.signal), row.signal_reason, _snapshot_from_json(row.portfolio_before), _intent_from_json(row.proposed_order), risk, order, fill, _snapshot_from_json(row.portfolio_after), row.outcome, row.error, _intent_from_json(row.execution_order), reconciliation or row.reconciliation_payload)


class SqlAlchemyOperationalRepository:
    """Transactional operational repository; no domain service sees ORM rows."""

    def __init__(self, database_url: str, *, create_schema: bool = False):
        if not database_url:
            raise PersistenceConfigurationError("DATABASE_URL is required when PostgreSQL trading persistence is enabled")
        normalized = database_url.replace("postgres://", "postgresql+psycopg://", 1) if database_url.startswith("postgres://") else database_url
        self.engine = create_engine(normalized, pool_pre_ping=True)
        if normalized.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        if create_schema:
            Base.metadata.create_all(self.engine)

    def ensure_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")

    def get(self, cycle_id: str) -> TradingDecision | None:
        with Session(self.engine) as session:
            row = session.get(TradingCycleRow, cycle_id)
            if row is None:
                return None
            order = session.scalar(select(OrderRow).where(OrderRow.cycle_id == cycle_id).order_by(OrderRow.updated_at.desc()))
            fill = session.scalar(select(FillRow).where(FillRow.order_id == order.id).order_by(FillRow.filled_at.desc())) if order else None
            return _decision_from_row(row, order, fill)

    def list(self) -> list[TradingDecision]:
        with Session(self.engine) as session:
            rows = session.scalars(select(TradingCycleRow).order_by(TradingCycleRow.created_at.desc())).all()
            decisions = []
            for row in rows:
                order = session.scalar(select(OrderRow).where(OrderRow.cycle_id == row.id).order_by(OrderRow.updated_at.desc()))
                fill = session.scalar(select(FillRow).where(FillRow.order_id == order.id).order_by(FillRow.filled_at.desc())) if order else None
                decisions.append(_decision_from_row(row, order, fill))
            return decisions

    def _save_decision_impl(self, decision: TradingDecision) -> None:
        now = decision.timestamp
        with Session(self.engine) as session:
            try:
                row = session.get(TradingCycleRow, decision.cycle_id)
                if row is None:
                    mode = "observation" if decision.outcome.startswith("WOULD") else "paper_execution"
                    row = TradingCycleRow(id=decision.cycle_id, strategy_name=decision.strategy, strategy_version=str(decision.strategy_parameters.get("version", "1")), symbol=decision.symbol, market_session=(decision.data_timestamp or decision.timestamp).isoformat(), mode=mode, signal=decision.signal.value, outcome=decision.outcome, created_at=now, updated_at=now)
                    session.add(row)
                row.signal = decision.signal.value; row.outcome = decision.outcome; row.updated_at = now; row.strategy_parameters = _json_value(decision.strategy_parameters); row.signal_reason = decision.signal_reason; row.risk_approved = decision.risk_decision.approved; row.risk_reason = decision.risk_decision.reason; row.error = decision.error; row.portfolio_before = _snapshot(decision.portfolio_before); row.portfolio_after = _snapshot(decision.portfolio_after); row.proposed_order = _intent(decision.proposed_order); row.execution_order = _intent(decision.execution_order); row.reconciliation_payload = _json_value(decision.reconciliation)
                session.flush()
                if decision.submitted_order:
                    order = session.get(OrderRow, decision.submitted_order.order_id)
                    intent = decision.submitted_order.intent
                    client_id = decision.submitted_order.client_order_id or intent.client_order_id or f"qi-{decision.cycle_id[:32]}"
                    if order is None:
                        order = OrderRow(id=decision.submitted_order.order_id, cycle_id=decision.cycle_id, client_order_id=client_id, broker_order_id=decision.submitted_order.broker_order_id, symbol=decision.symbol, side=intent.side.value, requested_quantity=decision.proposed_order.quantity if decision.proposed_order else intent.quantity, submitted_quantity=intent.quantity, filled_quantity=decision.submitted_order.filled_quantity, average_fill_price=decision.submitted_order.average_fill_price, status=decision.submitted_order.status, submitted_at=decision.submitted_order.submitted_at, updated_at=now)
                        session.add(order)
                    else:
                        order.status = decision.submitted_order.status; order.filled_quantity = decision.submitted_order.filled_quantity; order.average_fill_price = decision.submitted_order.average_fill_price; order.broker_order_id = decision.submitted_order.broker_order_id; order.updated_at = now
                    session.flush()
                    if decision.fill:
                        fill_id = f"{order.id}:{decision.fill.filled_at.isoformat()}:{decision.fill.quantity}:{decision.fill.price}"
                        if session.get(FillRow, fill_id) is None:
                            session.add(FillRow(id=fill_id, order_id=order.id, quantity=decision.fill.quantity, price=decision.fill.price, transaction_cost=decision.fill.transaction_cost, filled_at=decision.fill.filled_at))
                snapshot = decision.portfolio_after
                if snapshot is not None:
                    session.execute(delete(ManagedPositionRow))
                    for position in snapshot.positions:
                        session.add(ManagedPositionRow(symbol=position.symbol, quantity=position.shares, average_price=position.average_price, broker_updated_at=now, last_reconciled_at=now))
                session.commit()
            except Exception:
                session.rollback()
                raise

    def load_status(self) -> OperationalStatus:
        with Session(self.engine) as session:
            row = session.get(OperationalStatusRow, 1)
            if row is None:
                return OperationalStatus()
            positions = tuple(Position(item.symbol, item.quantity, item.average_price or 0.0) for item in session.scalars(select(ManagedPositionRow)).all())
            return OperationalStatus(state=row.service_state, last_cycle_id=row.last_cycle_id, last_cycle_timestamp=row.last_cycle_timestamp, last_cycle_outcome=row.last_cycle_outcome, last_error=row.last_error, current_equity=row.current_equity, current_cash=row.current_cash, current_positions=positions, most_recent_market_data_timestamp=row.most_recent_market_data_timestamp, mode=row.mode, execution_enabled=row.execution_enabled, broker_connected=row.broker_connected, last_reconciliation_status=None, unresolved_symbols=tuple(), broker_health=row.broker_health, market_data_health=row.market_data_healthy, latest_completed_session=row.latest_completed_session, next_scheduled_decision=row.next_decision_at, last_reconciliation_timestamp=row.last_reconciliation_at, unresolved_order_count=row.unresolved_order_count, trading_health=row.trading_state, halt_reason=row.halt_reason, managed_symbols=tuple(row.managed_symbols or []))

    def save_status(self, status: OperationalStatus) -> None:
        updated = status.last_cycle_timestamp or status.next_scheduled_decision or datetime.now().astimezone()
        with Session(self.engine) as session:
            row = session.get(OperationalStatusRow, 1) or OperationalStatusRow(id=1, service_state=status.state, trading_state=status.trading_health, market_data_healthy=status.market_data_health, updated_at=updated)
            row.service_state = status.state; row.trading_state = status.trading_health; row.halt_reason = status.halt_reason; row.broker_connected = status.broker_connected; row.market_data_healthy = status.market_data_health; row.last_cycle_id = status.last_cycle_id; row.last_cycle_timestamp = status.last_cycle_timestamp; row.last_reconciliation_at = status.last_reconciliation_timestamp; row.latest_completed_session = status.latest_completed_session; row.next_decision_at = status.next_scheduled_decision; row.unresolved_order_count = status.unresolved_order_count; row.last_cycle_outcome = status.last_cycle_outcome; row.last_error = status.last_error; row.current_equity = status.current_equity; row.current_cash = status.current_cash; row.most_recent_market_data_timestamp = status.most_recent_market_data_timestamp; row.mode = status.mode; row.execution_enabled = status.execution_enabled; row.broker_health = status.broker_health; row.trading_health = status.trading_health; row.managed_symbols = list(status.managed_symbols); row.updated_at = updated
            session.add(row); session.commit()

    # StatusStore-compatible aliases keep AutonomousTrader independent of the
    # persistence implementation.
    def load(self) -> OperationalStatus:
        return self.load_status()

    def save(self, value: TradingDecision | OperationalStatus) -> None:  # type: ignore[override]
        if isinstance(value, OperationalStatus):
            self.save_status(value)
            return
        self.save_decision(value)

    def save_decision(self, decision: TradingDecision) -> None:
        self._save_decision_impl(decision)

    def save_reconciliation(self, result: Any) -> None:
        with Session(self.engine) as session:
            cycle = session.get(TradingCycleRow, result.cycle_id)
            order = session.scalar(select(OrderRow).where(OrderRow.client_order_id == result.client_order_id))
            session.add(ReconciliationRow(id=f"{result.cycle_id}:{result.last_reconciled_at.isoformat()}", cycle_id=cycle.id if cycle else None, order_id=order.id if order else None, symbol=result.symbol, expected_quantity=result.expected_quantity, observed_quantity=result.observed_filled_quantity, status=result.status, action_taken="persisted before broker mirror synchronization", reconciled_at=result.last_reconciled_at))
            if result.observed_position is not None:
                current = session.get(ManagedPositionRow, result.symbol)
                if current is None:
                    current = ManagedPositionRow(symbol=result.symbol, quantity=result.observed_position, average_price=None)
                    session.add(current)
                else:
                    current.quantity = result.observed_position
                current.last_reconciled_at = result.last_reconciled_at
            session.commit()


class JsonOperationalRepository:
    """Compatibility adapter retained for explicit pre-cutover JSON mode."""

    def __init__(self, audit_dir: str | Path):
        self.audit = TradingAuditStore(audit_dir)
        self.status = StatusStore(Path(audit_dir) / "status.json")

    def get(self, cycle_id: str) -> TradingDecision | None:
        return self.audit.get(cycle_id)

    def list(self) -> list[TradingDecision]:
        decisions = []
        for path in self.audit.root.glob("*.json"):
            try:
                value = self.audit.get(path.stem)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                value = None
            if value:
                decisions.append(value)
        return sorted(decisions, key=lambda item: item.timestamp, reverse=True)

    def save(self, decision: TradingDecision) -> None:
        self.audit.save(decision)

    def load_status(self) -> OperationalStatus:
        return self.status.load()

    def save_status(self, status: OperationalStatus) -> None:
        self.status.save(status)

    def load(self) -> OperationalStatus:
        return self.load_status()

    def save(self, value: TradingDecision | OperationalStatus) -> None:  # type: ignore[override]
        if isinstance(value, OperationalStatus):
            self.save_status(value)
        else:
            self.audit.save(value)

    def save_reconciliation(self, result: Any) -> None:
        payload = asdict(result)
        payload["last_reconciled_at"] = result.last_reconciled_at.isoformat()
        path = self.audit.root / "reconciliations"
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{result.cycle_id}-{result.last_reconciled_at.timestamp()}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def operational_repository(audit_dir: str | Path, *, database_url: str | None = None, mode: str | None = None) -> OperationalRepository:
    selected = (mode or os.getenv("QI_TRADING_PERSISTENCE", "json")).lower()
    if selected not in {"json", "postgres"}:
        raise PersistenceConfigurationError("QI_TRADING_PERSISTENCE must be json or postgres")
    if selected == "postgres":
        url = database_url or os.getenv("DATABASE_URL")
        if not url:
            raise PersistenceConfigurationError("DATABASE_URL is required when QI_TRADING_PERSISTENCE=postgres")
        return SqlAlchemyOperationalRepository(url)
    return JsonOperationalRepository(audit_dir)
