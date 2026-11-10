"""Allow a third passport level, "project": a single central project reported to MoSPI.

Revision ID: 0010_infra_central_projects
Revises: 0009_infra_status_length
"""

from alembic import op

revision = "0010_infra_central_projects"
down_revision = "0009_infra_status_length"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("infra_level_valid", "infra_projects", type_="check")
    op.create_check_constraint("infra_level_valid", "infra_projects", "level IN ('programme', 'work', 'project')")


def downgrade() -> None:
    op.execute("DELETE FROM infra_projects WHERE level = 'project'")
    op.drop_constraint("infra_level_valid", "infra_projects", type_="check")
    op.create_check_constraint("infra_level_valid", "infra_projects", "level IN ('programme', 'work')")
