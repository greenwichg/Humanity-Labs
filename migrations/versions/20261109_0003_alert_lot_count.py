"""Store the number of batches on each alert.

Counting through alert_lots on every page view became slow once device
recalls listing thousands of serial numbers were ingested.

Revision ID: 0003_alert_lot_count
Revises: 0002_bound_search_vector
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_alert_lot_count"
down_revision = "0002_bound_search_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("lot_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.execute("UPDATE alerts SET lot_count = c.n FROM (SELECT alert_id, count(*) AS n FROM alert_lots GROUP BY alert_id) c "
               "WHERE c.alert_id = alerts.id")


def downgrade() -> None:
    op.drop_column("alerts", "lot_count")
