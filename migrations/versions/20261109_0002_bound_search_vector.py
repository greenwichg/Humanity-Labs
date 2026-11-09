"""Bound the text fed to the alert search vector.

Some device recalls list thousands of serial numbers, which overflowed
PostgreSQL's 1 MB tsvector limit. PostgreSQL 16 cannot change a generated
column's expression in place, so the column and its index are recreated.

Revision ID: 0002_bound_search_vector
Revises: 730a3f18ba9a
"""

from alembic import op

revision = "0002_bound_search_vector"
down_revision = "730a3f18ba9a"
branch_labels = None
depends_on = None

BOUNDED = (
    "to_tsvector('simple', left(coalesce(title, ''), 1000) || ' ' || left(coalesce(product_description, ''), 20000) "
    "|| ' ' || left(coalesce(manufacturer, ''), 500) || ' ' || left(coalesce(reason, ''), 5000) || ' ' || "
    "left(coalesce(lots_text, ''), 20000))"
)
UNBOUNDED = (
    "to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(product_description, '') || ' ' || "
    "coalesce(manufacturer, '') || ' ' || coalesce(reason, '') || ' ' || coalesce(lots_text, ''))"
)


def _replace(expression: str) -> None:
    op.execute("DROP INDEX IF EXISTS ix_alerts_search")
    op.execute("ALTER TABLE alerts DROP COLUMN search")
    op.execute(f"ALTER TABLE alerts ADD COLUMN search tsvector GENERATED ALWAYS AS ({expression}) STORED NOT NULL")
    op.execute("CREATE INDEX ix_alerts_search ON alerts USING gin (search)")


def upgrade() -> None:
    _replace(BOUNDED)


def downgrade() -> None:
    _replace(UNBOUNDED)
