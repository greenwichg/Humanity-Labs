"""initial schema

Revision ID: 730a3f18ba9a
Revises: 
Create Date: 2026-11-09 05:13:41.596564

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '730a3f18ba9a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table('organizations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('notify_email', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('pharmacy', 'clinic', 'hospital', 'distributor', 'household', 'ngo', 'other')", name='kind_valid'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sources',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('regulator', sa.String(length=100), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('homepage_url', sa.Text(), nullable=False),
    sa.Column('automated', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('is_curator', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('alerts',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('source_id', sa.String(length=40), nullable=False),
    sa.Column('external_id', sa.String(length=300), nullable=False),
    sa.Column('regulator', sa.String(length=100), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('alert_type', sa.String(length=20), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('classification', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=100), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('product_description', sa.Text(), nullable=True),
    sa.Column('product_names', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('active_ingredients', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('manufacturer', sa.Text(), nullable=True),
    sa.Column('manufacturer_key', sa.Text(), nullable=True),
    sa.Column('product_codes', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('name_tokens', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('distribution', sa.Text(), nullable=True),
    sa.Column('lots_text', sa.Text(), nullable=True),
    sa.Column('all_lots', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('published_at', sa.Date(), nullable=True),
    sa.Column('initiated_at', sa.Date(), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('search', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(product_description, '') || ' ' || coalesce(manufacturer, '') || ' ' || coalesce(reason, '') || ' ' || coalesce(lots_text, ''))", persisted=True), nullable=False),
    sa.CheckConstraint("alert_type IN ('recall', 'falsified', 'substandard', 'quality_defect', 'safety_notice')", name='alert_type_valid'),
    sa.CheckConstraint("severity IN ('critical', 'high', 'moderate', 'low', 'info')", name='severity_valid'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_alert_source_external')
    )
    op.create_index('ix_alerts_manufacturer_key', 'alerts', ['manufacturer_key'], unique=False)
    op.create_index('ix_alerts_name_tokens', 'alerts', ['name_tokens'], unique=False, postgresql_using='gin')
    op.create_index('ix_alerts_product_codes', 'alerts', ['product_codes'], unique=False, postgresql_using='gin')
    op.create_index('ix_alerts_published', 'alerts', ['published_at'], unique=False)
    op.create_index('ix_alerts_search', 'alerts', ['search'], unique=False, postgresql_using='gin')
    op.create_table('api_keys',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('prefix')
    )
    op.create_index(op.f('ix_api_keys_org_id'), 'api_keys', ['org_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=True),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('subject', sa.String(length=320), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_action_created', 'audit_events', ['action', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_events_org_id'), 'audit_events', ['org_id'], unique=False)
    op.create_table('holdings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('product_name', sa.String(length=300), nullable=False),
    sa.Column('active_ingredient', sa.String(length=300), nullable=True),
    sa.Column('strength', sa.String(length=100), nullable=True),
    sa.Column('dosage_form', sa.String(length=100), nullable=True),
    sa.Column('manufacturer', sa.String(length=300), nullable=True),
    sa.Column('product_code', sa.String(length=50), nullable=True),
    sa.Column('lot', sa.String(length=100), nullable=True),
    sa.Column('lot_key', sa.String(length=100), nullable=True),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=True),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('name_tokens', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('quantity IS NULL OR quantity >= 0', name='quantity_nonnegative'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_holdings_lot_key', 'holdings', ['lot_key'], unique=False)
    op.create_index('ix_holdings_name_tokens', 'holdings', ['name_tokens'], unique=False, postgresql_using='gin')
    op.create_index('ix_holdings_org_active', 'holdings', ['org_id'], unique=False, postgresql_where=sa.text('archived_at IS NULL'))
    op.create_table('ingestion_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('source_id', sa.String(length=40), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('fetched', sa.Integer(), nullable=False),
    sa.Column('created', sa.Integer(), nullable=False),
    sa.Column('updated', sa.Integer(), nullable=False),
    sa.Column('matched', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name='status_valid'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ingestion_runs_source_id'), 'ingestion_runs', ['source_id'], unique=False)
    op.create_table('invitations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('invited_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('owner', 'manager', 'member')", name='role_valid'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_invitations_org_id'), 'invitations', ['org_id'], unique=False)
    op.create_table('memberships',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('owner', 'manager', 'member')", name='role_valid'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'org_id')
    )
    op.create_index(op.f('ix_memberships_org_id'), 'memberships', ['org_id'], unique=False)
    op.create_table('password_resets',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_password_resets_user_id'), 'password_resets', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('active_org_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['active_org_id'], ['organizations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('alert_lots',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('alert_id', sa.UUID(), nullable=False),
    sa.Column('lot', sa.String(length=100), nullable=False),
    sa.Column('lot_key', sa.String(length=100), nullable=False),
    sa.Column('expiry_text', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_lots_alert_id'), 'alert_lots', ['alert_id'], unique=False)
    op.create_index(op.f('ix_alert_lots_lot_key'), 'alert_lots', ['lot_key'], unique=False)
    op.create_table('matches',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('alert_id', sa.UUID(), nullable=False),
    sa.Column('holding_id', sa.UUID(), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('confidence', sa.Integer(), nullable=False),
    sa.Column('reasons', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
    sa.Column('outcome', sa.String(length=20), nullable=True),
    sa.Column('units_affected', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.UUID(), nullable=True),
    sa.CheckConstraint("level IN ('batch', 'product')", name='level_valid'),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('quarantined', 'returned', 'destroyed', 'patients_notified', 'not_in_stock', 'false_match', 'other')", name='outcome_valid'),
    sa.CheckConstraint("status IN ('open', 'resolved')", name='status_valid'),
    sa.CheckConstraint('units_affected IS NULL OR units_affected >= 0', name='units_nonnegative'),
    sa.ForeignKeyConstraint(['alert_id'], ['alerts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['holding_id'], ['holdings.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('alert_id', 'holding_id', name='uq_match_alert_holding')
    )
    op.create_index(op.f('ix_matches_alert_id'), 'matches', ['alert_id'], unique=False)
    op.create_index(op.f('ix_matches_holding_id'), 'matches', ['holding_id'], unique=False)
    op.create_index('ix_matches_org', 'matches', ['org_id'], unique=False)
    op.create_index('ix_matches_org_open', 'matches', ['org_id', 'created_at'], unique=False, postgresql_where=sa.text("status = 'open'"))
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.Column('match_id', sa.UUID(), nullable=True),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('in_app', 'email')", name='channel_valid'),
    sa.CheckConstraint("status IN ('pending', 'sent', 'failed', 'skipped')", name='status_valid'),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_index('ix_notifications_user_unread', 'notifications', ['user_id'], unique=False, postgresql_where=sa.text("read_at IS NULL AND channel = 'in_app'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_notifications_user_unread', table_name='notifications', postgresql_where=sa.text("read_at IS NULL AND channel = 'in_app'"))
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('ix_matches_org_open', table_name='matches', postgresql_where=sa.text("status = 'open'"))
    op.drop_index('ix_matches_org', table_name='matches')
    op.drop_index(op.f('ix_matches_holding_id'), table_name='matches')
    op.drop_index(op.f('ix_matches_alert_id'), table_name='matches')
    op.drop_table('matches')
    op.drop_index(op.f('ix_alert_lots_lot_key'), table_name='alert_lots')
    op.drop_index(op.f('ix_alert_lots_alert_id'), table_name='alert_lots')
    op.drop_table('alert_lots')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_password_resets_user_id'), table_name='password_resets')
    op.drop_table('password_resets')
    op.drop_index(op.f('ix_memberships_org_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_invitations_org_id'), table_name='invitations')
    op.drop_table('invitations')
    op.drop_index(op.f('ix_ingestion_runs_source_id'), table_name='ingestion_runs')
    op.drop_table('ingestion_runs')
    op.drop_index('ix_holdings_org_active', table_name='holdings', postgresql_where=sa.text('archived_at IS NULL'))
    op.drop_index('ix_holdings_name_tokens', table_name='holdings', postgresql_using='gin')
    op.drop_index('ix_holdings_lot_key', table_name='holdings')
    op.drop_table('holdings')
    op.drop_index(op.f('ix_audit_events_org_id'), table_name='audit_events')
    op.drop_index('ix_audit_action_created', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_api_keys_org_id'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_index('ix_alerts_search', table_name='alerts', postgresql_using='gin')
    op.drop_index('ix_alerts_published', table_name='alerts')
    op.drop_index('ix_alerts_product_codes', table_name='alerts', postgresql_using='gin')
    op.drop_index('ix_alerts_name_tokens', table_name='alerts', postgresql_using='gin')
    op.drop_index('ix_alerts_manufacturer_key', table_name='alerts')
    op.drop_table('alerts')
    op.drop_table('users')
    op.drop_table('sources')
    op.drop_table('organizations')
