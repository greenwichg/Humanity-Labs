"""Allow the "official_copy" evidence label: official data republished by another organisation.

Revision ID: 0008_infra_official_copy
Revises: 0007_public_infrastructure
"""

from alembic import op

revision = "0008_infra_official_copy"
down_revision = "0007_public_infrastructure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("infra_event_evidence_valid", "infra_events", type_="check")
    op.create_check_constraint(
        "infra_event_evidence_valid",
        "infra_events",
        "evidence IN ('official', 'official_copy', 'official_finding', 'calculated', 'report', 'citizen')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM infra_events WHERE evidence = 'official_copy'")
    op.drop_constraint("infra_event_evidence_valid", "infra_events", type_="check")
    op.create_check_constraint(
        "infra_event_evidence_valid",
        "infra_events",
        "evidence IN ('official', 'official_finding', 'calculated', 'report', 'citizen')",
    )
