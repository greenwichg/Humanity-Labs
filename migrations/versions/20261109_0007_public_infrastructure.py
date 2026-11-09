"""Public infrastructure: Project Passports, their events and contractors, and audit documents.

New tables only; medicine and food tables are untouched.

Revision ID: 0007_public_infrastructure
Revises: 0006_alert_evidence
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_public_infrastructure"
down_revision = "0006_alert_evidence"
branch_labels = None
depends_on = None

SEARCH = (
    "to_tsvector('simple', left(coalesce(title, ''), 1000) || ' ' || left(coalesce(description, ''), 4000) || ' ' || "
    "left(coalesce(implementing_agency, ''), 1000) || ' ' || left(coalesce(tendering_body, ''), 500) || ' ' || "
    "left(coalesce(contractors_text, ''), 1000) || ' ' || coalesce(external_id, ''))"
)


def upgrade() -> None:
    op.create_table(
        "infra_projects",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.String(length=40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("parent_external_id", sa.String(length=200), nullable=True),
        sa.Column("parent_id", sa.UUID(), sa.ForeignKey("infra_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("asset_type", sa.String(length=20), server_default="other", nullable=False),
        sa.Column("state_slugs", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("state_basis", sa.String(length=20), nullable=True),
        sa.Column("funder", sa.Text(), nullable=True),
        sa.Column("borrower", sa.Text(), nullable=True),
        sa.Column("implementing_agency", sa.Text(), nullable=True),
        sa.Column("tendering_body", sa.Text(), nullable=True),
        sa.Column("budget_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("budget_currency", sa.String(length=3), nullable=True),
        sa.Column("funder_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("funder_currency", sa.String(length=3), nullable=True),
        sa.Column("contract_amount", sa.Numeric(20, 2), nullable=True),
        sa.Column("contract_currency", sa.String(length=3), nullable=True),
        sa.Column("contractors_text", sa.Text(), nullable=True),
        sa.Column("procurement_method", sa.String(length=100), nullable=True),
        sa.Column("bids_evaluated", sa.Integer(), nullable=True),
        sa.Column("bids_rejected", sa.Integer(), nullable=True),
        sa.Column("approved_on", sa.Date(), nullable=True),
        sa.Column("tendered_on", sa.Date(), nullable=True),
        sa.Column("bid_deadline_on", sa.Date(), nullable=True),
        sa.Column("awarded_on", sa.Date(), nullable=True),
        sa.Column("duration_text", sa.String(length=50), nullable=True),
        sa.Column("promised_completion_on", sa.Date(), nullable=True),
        sa.Column("promised_basis", sa.String(length=20), nullable=True),
        sa.Column("actual_completion_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("tender_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("data", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("search", postgresql.TSVECTOR(), sa.Computed(SEARCH, persisted=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_infra_project_source_external"),
        sa.CheckConstraint("level IN ('programme', 'work')", name="infra_level_valid"),
    )
    op.create_index("ix_infra_projects_states", "infra_projects", ["state_slugs"], postgresql_using="gin")
    op.create_index("ix_infra_projects_search", "infra_projects", ["search"], postgresql_using="gin")
    op.create_index("ix_infra_projects_type_awarded", "infra_projects", ["asset_type", "awarded_on"])
    op.create_index("ix_infra_projects_parent", "infra_projects", ["parent_id"])

    op.create_table(
        "infra_parties",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="contractor", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "kind", name="uq_infra_party_key_kind"),
    )
    op.create_table(
        "infra_project_parties",
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("infra_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("party_id", sa.UUID(), sa.ForeignKey("infra_parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "party_id", "role"),
    )
    op.create_index("ix_infra_project_parties_party_id", "infra_project_parties", ["party_id"])
    op.create_table(
        "infra_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("infra_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("evidence", sa.String(length=20), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "key", name="uq_infra_event_project_key"),
        sa.CheckConstraint(
            "evidence IN ('official', 'official_finding', 'calculated', 'report', 'citizen')", name="infra_event_evidence_valid"
        ),
    )
    op.create_table(
        "infra_documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.String(length=40), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), server_default="audit_report", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=True),
        sa.Column("sectors", sa.Text(), nullable=True),
        sa.Column("state_slugs", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("published_on", sa.Date(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_infra_document_source_external"),
    )
    op.create_index("ix_infra_documents_states", "infra_documents", ["state_slugs"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_infra_documents_states", table_name="infra_documents")
    op.drop_table("infra_documents")
    op.drop_table("infra_events")
    op.drop_index("ix_infra_project_parties_party_id", table_name="infra_project_parties")
    op.drop_table("infra_project_parties")
    op.drop_table("infra_parties")
    op.drop_index("ix_infra_projects_parent", table_name="infra_projects")
    op.drop_index("ix_infra_projects_type_awarded", table_name="infra_projects")
    op.drop_index("ix_infra_projects_search", table_name="infra_projects")
    op.drop_index("ix_infra_projects_states", table_name="infra_projects")
    op.drop_table("infra_projects")
