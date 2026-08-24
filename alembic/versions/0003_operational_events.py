"""add small durable lifecycle journal for soak observability"""
from alembic import op
import sqlalchemy as sa

revision = "0003_operational_events"
down_revision = "0002_durable_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("event_key", sa.String(256), unique=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("service_state", sa.String(32)),
        sa.Column("trading_health", sa.String(32)),
        sa.Column("risk_mode", sa.String(32)),
        sa.Column("reason", sa.String(2000)),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operational_events_created_at", "operational_events", ["created_at"])
    op.create_index("ix_operational_events_type_created", "operational_events", ["event_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_operational_events_type_created", table_name="operational_events")
    op.drop_index("ix_operational_events_created_at", table_name="operational_events")
    op.drop_table("operational_events")
