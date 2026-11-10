"""Contractors and agencies are scoped to one source and one state, and linked to projects by role.

Before this, every record with the same printed contractor name was joined into one profile, across sources
and states. That treats a shared name as proof of a shared identity, which it is not. Existing links are moved
to scoped rows: (name key, kind, source, the project's single state or ""). Agency roles are filled in by the
application from stored project fields after each ingestion run.

Revision ID: 0011_infra_party_scope_roles
Revises: 0010_infra_central_projects
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_infra_party_scope_roles"
down_revision = "0010_infra_central_projects"
branch_labels = None
depends_on = None

SCOPE_STATE = "CASE WHEN cardinality(pr.state_slugs) = 1 THEN pr.state_slugs[1] ELSE '' END"


def upgrade() -> None:
    op.add_column("infra_parties", sa.Column("scope_source", sa.String(length=40), server_default="", nullable=False))
    op.add_column("infra_parties", sa.Column("scope_state", sa.String(length=40), server_default="", nullable=False))
    op.drop_constraint("uq_infra_party_key_kind", "infra_parties", type_="unique")
    op.execute(
        f"""
        CREATE TEMP TABLE infra_links_rescope ON COMMIT DROP AS
        SELECT pp.project_id, pp.role, p.key, p.name, p.kind, pr.source_id AS scope_source, {SCOPE_STATE} AS scope_state
        FROM infra_project_parties pp
        JOIN infra_parties p ON p.id = pp.party_id
        JOIN infra_projects pr ON pr.id = pp.project_id
        """
    )
    op.execute("DELETE FROM infra_project_parties")
    op.execute("DELETE FROM infra_parties")
    op.create_unique_constraint("uq_infra_party_scope", "infra_parties", ["key", "kind", "scope_source", "scope_state"])
    op.execute(
        """
        INSERT INTO infra_parties (id, key, name, kind, scope_source, scope_state, created_at)
        SELECT gen_random_uuid(), key, min(name), kind, scope_source, scope_state, now()
        FROM infra_links_rescope GROUP BY key, kind, scope_source, scope_state
        """
    )
    op.execute(
        """
        INSERT INTO infra_project_parties (project_id, party_id, role)
        SELECT l.project_id, p.id, l.role FROM infra_links_rescope l
        JOIN infra_parties p ON p.key = l.key AND p.kind = l.kind AND p.scope_source = l.scope_source AND p.scope_state = l.scope_state
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM infra_project_parties WHERE role <> 'contractor'")
    op.execute("DELETE FROM infra_parties WHERE kind <> 'contractor'")
    op.execute(
        """
        CREATE TEMP TABLE infra_links_merge ON COMMIT DROP AS
        SELECT pp.project_id, pp.role, p.key, p.name, p.kind FROM infra_project_parties pp JOIN infra_parties p ON p.id = pp.party_id
        """
    )
    op.execute("DELETE FROM infra_project_parties")
    op.execute("DELETE FROM infra_parties")
    op.drop_constraint("uq_infra_party_scope", "infra_parties", type_="unique")
    op.drop_column("infra_parties", "scope_state")
    op.drop_column("infra_parties", "scope_source")
    op.create_unique_constraint("uq_infra_party_key_kind", "infra_parties", ["key", "kind"])
    op.execute(
        "INSERT INTO infra_parties (id, key, name, kind, created_at) "
        "SELECT gen_random_uuid(), key, min(name), kind, now() FROM infra_links_merge GROUP BY key, kind"
    )
    op.execute(
        "INSERT INTO infra_project_parties (project_id, party_id, role) SELECT l.project_id, p.id, l.role "
        "FROM infra_links_merge l JOIN infra_parties p ON p.key = l.key AND p.kind = l.kind ON CONFLICT DO NOTHING"
    )
