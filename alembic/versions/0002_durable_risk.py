"""durable portfolio risk constitution"""
from alembic import op
import sqlalchemy as sa

revision = "0002_durable_risk"
down_revision = "0001_operational_trading"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("operational_status", sa.Column("risk_mode", sa.String(32), nullable=False, server_default="NORMAL"))
    op.add_column("trading_cycles", sa.Column("risk_mode", sa.String(32), nullable=False, server_default="NORMAL"))
    op.add_column("trading_cycles", sa.Column("risk_event_type", sa.String(64), nullable=True))
    op.create_table("risk_state",
        sa.Column("account_key", sa.String(128), primary_key=True),
        sa.Column("session_id", sa.String(128)),
        sa.Column("daily_baseline_equity", sa.Float()),
        sa.Column("high_water_equity", sa.Float()),
        sa.Column("risk_mode", sa.String(32), nullable=False),
        sa.Column("daily_latched", sa.Boolean(), nullable=False),
        sa.Column("critical_latched", sa.Boolean(), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("reset_reason", sa.String(2000)),
        sa.Column("reset_at", sa.DateTime(timezone=True)),
        sa.Column("transition_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reset_operator", sa.String(256)),
        sa.CheckConstraint("risk_mode IN ('NORMAL', 'DAILY_HALT', 'CRITICAL_HALT')", name="ck_risk_state_mode"),
        sa.CheckConstraint("daily_baseline_equity IS NULL OR daily_baseline_equity > 0", name="ck_risk_state_baseline_positive"),
        sa.CheckConstraint("high_water_equity IS NULL OR high_water_equity > 0", name="ck_risk_state_hwm_positive"),
        sa.CheckConstraint("transition_sequence >= 0", name="ck_risk_state_transition_sequence_nonnegative"))
    op.create_table("risk_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("account_key", sa.String(128), nullable=False),
        sa.Column("event_key", sa.String(256), nullable=False, unique=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(128)),
        sa.Column("equity", sa.Float()),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("equity IS NULL OR equity > 0", name="ck_risk_event_equity_positive"))
    op.create_index("ix_risk_events_account_created", "risk_events", ["account_key", "created_at"])
    op.create_index("ix_risk_events_type_created", "risk_events", ["event_type", "created_at"])

def downgrade() -> None:
    op.drop_index("ix_risk_events_type_created", table_name="risk_events")
    op.drop_index("ix_risk_events_account_created", table_name="risk_events")
    op.drop_table("risk_events")
    op.drop_table("risk_state")
    op.drop_column("trading_cycles", "risk_mode")
    op.drop_column("trading_cycles", "risk_event_type")
    op.drop_column("operational_status", "risk_mode")
