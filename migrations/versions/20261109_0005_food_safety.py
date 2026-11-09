"""Food Safety module: categories for sources and alerts, consumer reports, testing labs.

Existing sources and alerts become category "medicine", so Batchlight is unchanged.

Revision ID: 0005_food_safety
Revises: 0004_alert_lots_index
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_food_safety"
down_revision = "0004_alert_lots_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("category", sa.String(length=20), server_default="medicine", nullable=False))
    op.add_column("alerts", sa.Column("category", sa.String(length=20), server_default="medicine", nullable=False))
    op.create_check_constraint("category_valid", "alerts", "category IN ('medicine', 'food')")
    op.create_index("ix_alerts_category_published", "alerts", ["category", "published_at"])

    op.create_table(
        "food_reports",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("reference", sa.String(length=16), nullable=False),
        sa.Column("problem", sa.String(length=30), nullable=False),
        sa.Column("product_name", sa.String(length=300), nullable=False),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("batch", sa.String(length=100), nullable=True),
        sa.Column("barcode", sa.String(length=20), nullable=True),
        sa.Column("fssai_licence", sa.String(length=20), nullable=True),
        sa.Column("purchased_at", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("contact_email", postgresql.CITEXT(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="new", nullable=False),
        sa.Column("curator_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "problem IN ('looks_wrong', 'made_ill', 'foreign_object', 'label_problem', 'suspected_fake', "
            "'expired_or_tampered', 'other')",
            name="problem_valid",
        ),
        sa.CheckConstraint("status IN ('new', 'reviewed', 'forwarded', 'dismissed')", name="status_valid"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index("ix_food_reports_status_created", "food_reports", ["status", "created_at"])
    op.create_index("ix_food_reports_ip_created", "food_reports", ["ip_hash", "created_at"])

    op.create_table(
        "testing_labs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=100), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("accreditation", sa.String(length=200), nullable=False),
        sa.Column("tests", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("accepts_public_samples", sa.Boolean(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("verified_on", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_testing_labs_country_active", "testing_labs", ["country_code", "active"])


def downgrade() -> None:
    op.drop_index("ix_testing_labs_country_active", table_name="testing_labs")
    op.drop_table("testing_labs")
    op.drop_index("ix_food_reports_ip_created", table_name="food_reports")
    op.drop_index("ix_food_reports_status_created", table_name="food_reports")
    op.drop_table("food_reports")
    op.drop_index("ix_alerts_category_published", table_name="alerts")
    op.drop_constraint("category_valid", "alerts", type_="check")
    op.drop_column("alerts", "category")
    op.drop_column("sources", "category")
