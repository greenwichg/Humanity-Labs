"""Label what each alert's source link is: an official notice, an official lab result or a news report.

Every existing alert becomes "official", except hand-added alerts that link to a news or other
non-government site, which become "report". The "Curated" classification pill is removed.

Revision ID: 0006_alert_evidence
Revises: 0005_food_safety
"""

from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op

revision = "0006_alert_evidence"
down_revision = "0005_food_safety"
branch_labels = None
depends_on = None

# A snapshot of batchlight.text.OFFICIAL_DOMAINS when this migration was written.
OFFICIAL_DOMAINS = ("gov", "gov.in", "nic.in", "sansad.in", "gc.ca", "canada.ca", "gov.uk", "gov.hk", "gov.sg", "who.int", "europa.eu")


def _official(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def upgrade() -> None:
    op.add_column("alerts", sa.Column("evidence", sa.String(length=10), server_default="official", nullable=False))
    op.create_check_constraint("evidence_valid", "alerts", "evidence IN ('official', 'lab_test', 'report')")
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, source_url FROM alerts WHERE source_id = 'curated'")).all()
    reports = [row.id for row in rows if not _official(row.source_url)]
    if reports:
        conn.execute(
            sa.text("UPDATE alerts SET evidence = 'report' WHERE id = ANY(:ids)"),
            {"ids": reports},
        )
    conn.execute(sa.text("UPDATE alerts SET classification = NULL WHERE source_id = 'curated' AND classification = 'Curated'"))


def downgrade() -> None:
    op.execute("UPDATE alerts SET classification = 'Curated' WHERE source_id = 'curated' AND classification IS NULL")
    op.drop_constraint("evidence_valid", "alerts", type_="check")
    op.drop_column("alerts", "evidence")
