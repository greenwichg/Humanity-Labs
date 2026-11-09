"""Room for long official stage names (PMGSY uses names longer than 50 characters).

Revision ID: 0009_infra_status_length
Revises: 0008_infra_official_copy
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_infra_status_length"
down_revision = "0008_infra_official_copy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("infra_projects", "status", type_=sa.String(length=200), existing_nullable=True)


def downgrade() -> None:
    op.alter_column("infra_projects", "status", type_=sa.String(length=50), existing_nullable=True)
