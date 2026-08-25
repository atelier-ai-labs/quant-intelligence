"""preserve requested session separately from observed market data timestamp"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "0004_authoritative_market_session"
down_revision = "0003_operational_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trading_cycles", sa.Column("market_data_timestamp", sa.DateTime(timezone=True), nullable=True))
    bind = op.get_bind()
    table = sa.table("trading_cycles", sa.column("id", sa.String), sa.column("market_session", sa.String))
    for row_id, value in bind.execute(sa.select(table.c.id, table.c.market_session)).all():
        try:
            canonical = datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except (AttributeError, TypeError, ValueError):
            continue
        bind.execute(sa.update(table).where(table.c.id == row_id).values(market_session=canonical))


def downgrade() -> None:
    op.drop_column("trading_cycles", "market_data_timestamp")
