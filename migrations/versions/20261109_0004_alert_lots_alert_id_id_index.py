"""Index alert_lots on (alert_id, id) for bounded batch previews.

Replaces the single-column alert_id index, which the composite index covers.

Revision ID: 0004_alert_lots_index
Revises: 0003_alert_lot_count
"""

from alembic import op

revision = "0004_alert_lots_index"
down_revision = "0003_alert_lot_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_alert_lots_alert_id_id", "alert_lots", ["alert_id", "id"])
    op.drop_index("ix_alert_lots_alert_id", table_name="alert_lots")


def downgrade() -> None:
    op.create_index("ix_alert_lots_alert_id", "alert_lots", ["alert_id"])
    op.drop_index("ix_alert_lots_alert_id_id", table_name="alert_lots")
