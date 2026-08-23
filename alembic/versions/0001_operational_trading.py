"""create operational trading schema"""
from alembic import op
import sqlalchemy as sa

revision = "0001_operational_trading"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("trading_cycles",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("strategy_name", sa.String(128), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("market_session", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("signal", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_parameters", sa.JSON(), nullable=False),
        sa.Column("signal_reason", sa.String(1000), nullable=False),
        sa.Column("risk_approved", sa.Boolean(), nullable=False),
        sa.Column("risk_reason", sa.String(1000), nullable=False),
        sa.Column("error", sa.String(2000)),
        sa.Column("portfolio_before", sa.JSON()),
        sa.Column("portfolio_after", sa.JSON()),
        sa.Column("proposed_order", sa.JSON()),
        sa.Column("execution_order", sa.JSON()),
        sa.Column("reconciliation_payload", sa.JSON()),
        sa.UniqueConstraint("strategy_name", "strategy_version", "symbol", "market_session", "mode", name="uq_trading_cycle_identity"))
    op.create_table("orders",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(128), sa.ForeignKey("trading_cycles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False, unique=True),
        sa.Column("broker_order_id", sa.String(128), unique=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("submitted_quantity", sa.Integer(), nullable=False),
        sa.Column("filled_quantity", sa.Integer(), nullable=False),
        sa.Column("average_fill_price", sa.Float()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("requested_quantity > 0", name="ck_orders_requested_positive"),
        sa.CheckConstraint("submitted_quantity > 0", name="ck_orders_submitted_positive"),
        sa.CheckConstraint("filled_quantity >= 0", name="ck_orders_filled_nonnegative"),
        sa.CheckConstraint("filled_quantity <= submitted_quantity", name="ck_orders_filled_lte_submitted"),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_orders_supported_side"))
    op.create_index("ix_orders_cycle_id", "orders", ["cycle_id"])
    op.create_table("fills",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("order_id", sa.String(128), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("broker_fill_id", sa.String(128), unique=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("transaction_cost", sa.Float(), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_fills_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_fills_price_positive"))
    op.create_index("ix_fills_order_id", "fills", ["order_id"])
    op.create_table("reconciliations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("cycle_id", sa.String(128), sa.ForeignKey("trading_cycles.id", ondelete="RESTRICT")),
        sa.Column("order_id", sa.String(128), sa.ForeignKey("orders.id", ondelete="RESTRICT")),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("expected_quantity", sa.Integer()),
        sa.Column("observed_quantity", sa.Integer()),
        sa.Column("expected_cash", sa.Float()),
        sa.Column("observed_cash", sa.Float()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("action_taken", sa.String(1000)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_reconciliations_cycle_id", "reconciliations", ["cycle_id"])
    op.create_index("ix_reconciliations_order_id", "reconciliations", ["order_id"])
    op.create_table("managed_positions",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_price", sa.Float()),
        sa.Column("broker_updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("quantity >= 0", name="ck_managed_positions_quantity_nonnegative"))
    op.create_table("operational_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service_state", sa.String(32), nullable=False),
        sa.Column("trading_state", sa.String(32), nullable=False),
        sa.Column("halt_reason", sa.String(2000)),
        sa.Column("broker_connected", sa.Boolean()),
        sa.Column("market_data_healthy", sa.String(32), nullable=False),
        sa.Column("last_cycle_id", sa.String(128)),
        sa.Column("last_cycle_timestamp", sa.DateTime(timezone=True)),
        sa.Column("last_reconciliation_at", sa.DateTime(timezone=True)),
        sa.Column("latest_completed_session", sa.String(64)),
        sa.Column("next_decision_at", sa.DateTime(timezone=True)),
        sa.Column("unresolved_order_count", sa.Integer(), nullable=False),
        sa.Column("last_cycle_outcome", sa.String(64)),
        sa.Column("last_error", sa.String(2000)),
        sa.Column("current_equity", sa.Float()),
        sa.Column("current_cash", sa.Float()),
        sa.Column("most_recent_market_data_timestamp", sa.DateTime(timezone=True)),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("execution_enabled", sa.Boolean(), nullable=False),
        sa.Column("broker_health", sa.String(32), nullable=False),
        sa.Column("trading_health", sa.String(32), nullable=False),
        sa.Column("managed_symbols", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))

def downgrade() -> None:
    op.drop_table("operational_status")
    op.drop_table("managed_positions")
    op.drop_index("ix_reconciliations_order_id", table_name="reconciliations")
    op.drop_index("ix_reconciliations_cycle_id", table_name="reconciliations")
    op.drop_table("reconciliations")
    op.drop_index("ix_fills_order_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index("ix_orders_cycle_id", table_name="orders")
    op.drop_table("orders")
    op.drop_table("trading_cycles")
