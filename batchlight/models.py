"""SQLAlchemy models. See docs/ARCHITECTURE.md for the data model rationale."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ORG_KINDS = ("pharmacy", "clinic", "hospital", "distributor", "household", "ngo", "other")
ROLES = ("owner", "manager", "member")
SEVERITIES = ("critical", "high", "moderate", "low", "info")
ALERT_TYPES = ("recall", "falsified", "substandard", "quality_defect", "safety_notice")
MATCH_LEVELS = ("batch", "product")
MATCH_STATUSES = ("open", "resolved")
OUTCOMES = ("quarantined", "returned", "destroyed", "patients_notified", "not_in_stock", "false_match", "other")
ACTION_OUTCOMES = ("quarantined", "returned", "destroyed", "patients_notified")
CATEGORIES = ("medicine", "food")
# What an alert's source link is: a regulator's own notice, a published official lab result, or a news or NGO report.
EVIDENCES = ("official", "lab_test", "report")
REPORT_PROBLEMS = ("looks_wrong", "made_ill", "foreign_object", "label_problem", "suspected_fake", "expired_or_tampered", "other")
REPORT_STATUSES = ("new", "reviewed", "forwarded", "dismissed")
LAB_TESTS = ("pesticides", "microbiology", "heavy_metals", "mycotoxins", "adulterants", "allergens", "nutrition", "water")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_curator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = created_at()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[Membership]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint(_in("kind", ORG_KINDS), name="kind_valid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    notify_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = created_at()

    memberships: Mapped[list[Membership]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role_valid"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = created_at()

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role_valid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship()


class Session_(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    active_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))

    user: Mapped[User] = relationship()


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    regulator: Mapped[str] = mapped_column(String(100), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))  # None for WHO / international
    homepage_url: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="medicine", server_default="medicine")
    automated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    __table_args__ = (CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_valid"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="running")
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_alert_source_external"),
        CheckConstraint(_in("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_in("alert_type", ALERT_TYPES), name="alert_type_valid"),
        CheckConstraint(_in("category", CATEGORIES), name="category_valid"),
        CheckConstraint(_in("evidence", EVIDENCES), name="evidence_valid"),
        Index("ix_alerts_category_published", "category", "published_at"),
        Index("ix_alerts_name_tokens", "name_tokens", postgresql_using="gin"),
        Index("ix_alerts_product_codes", "product_codes", postgresql_using="gin"),
        Index("ix_alerts_search", "search", postgresql_using="gin"),
        Index("ix_alerts_published", "published_at"),
        Index("ix_alerts_manufacturer_key", "manufacturer_key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    regulator: Mapped[str] = mapped_column(String(100), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="medicine", server_default="medicine")
    evidence: Mapped[str] = mapped_column(String(10), nullable=False, default="official", server_default="official")
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    classification: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    product_description: Mapped[str | None] = mapped_column(Text)
    product_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    active_ingredients: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    manufacturer: Mapped[str | None] = mapped_column(Text)
    manufacturer_key: Mapped[str | None] = mapped_column(Text)
    product_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    name_tokens: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    reason: Mapped[str | None] = mapped_column(Text)
    distribution: Mapped[str | None] = mapped_column(Text)
    lots_text: Mapped[str | None] = mapped_column(Text)
    all_lots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    lot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    published_at: Mapped[date | None] = mapped_column(Date)
    initiated_at: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Deferred: source payloads and the search vector can be large and are never needed when listing alerts.
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", deferred=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', left(coalesce(title, ''), 1000) || ' ' || left(coalesce(product_description, ''), 20000) "
            "|| ' ' || left(coalesce(manufacturer, ''), 500) || ' ' || left(coalesce(reason, ''), 5000) || ' ' || "
            "left(coalesce(lots_text, ''), 20000))",
            persisted=True,
        ),
        deferred=True,
    )

    source: Mapped[Source] = relationship()
    lots: Mapped[list[AlertLot]] = relationship(back_populates="alert", cascade="all, delete-orphan", order_by="AlertLot.id")


class AlertLot(Base):
    __tablename__ = "alert_lots"
    # (alert_id, id) serves both "batches of this alert" and "first N batches of this alert" without sorting.
    __table_args__ = (Index("ix_alert_lots_alert_id_id", "alert_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    lot: Mapped[str] = mapped_column(String(100), nullable=False)
    lot_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    expiry_text: Mapped[str | None] = mapped_column(String(100))

    alert: Mapped[Alert] = relationship(back_populates="lots")


class Holding(Base):
    __tablename__ = "holdings"
    __table_args__ = (
        Index("ix_holdings_name_tokens", "name_tokens", postgresql_using="gin"),
        Index("ix_holdings_lot_key", "lot_key"),
        Index("ix_holdings_org_active", "org_id", postgresql_where=text("archived_at IS NULL")),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="quantity_nonnegative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(300), nullable=False)
    active_ingredient: Mapped[str | None] = mapped_column(String(300))
    strength: Mapped[str | None] = mapped_column(String(100))
    dosage_form: Mapped[str | None] = mapped_column(String(100))
    manufacturer: Mapped[str | None] = mapped_column(String(300))
    product_code: Mapped[str | None] = mapped_column(String(50))
    lot: Mapped[str | None] = mapped_column(String(100))
    lot_key: Mapped[str | None] = mapped_column(String(100))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    quantity: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    name_tokens: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("alert_id", "holding_id", name="uq_match_alert_holding"),
        CheckConstraint(_in("level", MATCH_LEVELS), name="level_valid"),
        CheckConstraint(_in("status", MATCH_STATUSES), name="status_valid"),
        CheckConstraint(f"outcome IS NULL OR {_in('outcome', OUTCOMES)}", name="outcome_valid"),
        CheckConstraint("units_affected IS NULL OR units_affected >= 0", name="units_nonnegative"),
        Index("ix_matches_org_open", "org_id", "created_at", postgresql_where=text("status = 'open'")),
        Index("ix_matches_org", "org_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    holding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", server_default="open")
    outcome: Mapped[str | None] = mapped_column(String(20))
    units_affected: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    alert: Mapped[Alert] = relationship()
    holding: Mapped[Holding] = relationship()
    resolver: Mapped[User | None] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app', 'email')", name="channel_valid"),
        CheckConstraint("status IN ('pending', 'sent', 'failed', 'skipped')", name="status_valid"),
        Index("ix_notifications_user_unread", "user_id", postgresql_where=text("read_at IS NULL AND channel = 'in_app'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_action_created", "action", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(String(320))  # e.g. email for login attempts
    ip: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class FoodReport(Base):
    """A consumer's report of a suspicious food product. Unverified: never published as fact."""

    __tablename__ = "food_reports"
    __table_args__ = (
        CheckConstraint(_in("problem", REPORT_PROBLEMS), name="problem_valid"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="status_valid"),
        Index("ix_food_reports_status_created", "status", "created_at"),
        Index("ix_food_reports_ip_created", "ip_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    reference: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    problem: Mapped[str] = mapped_column(String(30), nullable=False)
    product_name: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200))
    batch: Mapped[str | None] = mapped_column(String(100))
    barcode: Mapped[str | None] = mapped_column(String(20))
    fssai_licence: Mapped[str | None] = mapped_column(String(20))
    purchased_at: Mapped[str | None] = mapped_column(String(200))  # shop or website, free text
    city: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(CITEXT)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="new", server_default="new")
    curator_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class TestingLab(Base):
    """A food-testing laboratory, added by a curator from an official accreditation or notification source."""

    __tablename__ = "testing_labs"
    __table_args__ = (Index("ix_testing_labs_country_active", "country_code", "active"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str | None] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    accreditation: Mapped[str] = mapped_column(String(200), nullable=False)  # e.g. "NABL TC-1234; FSSAI notified"
    tests: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    accepts_public_samples: Mapped[bool | None] = mapped_column(Boolean)
    website: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)  # where the accreditation was checked
    verified_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --------------------------------------------------------------------------- public infrastructure

INFRA_LEVELS = ("programme", "work")
INFRA_EVIDENCE = ("official", "official_copy", "official_finding", "calculated", "report", "citizen")


class InfraProject(Base):
    """A Project Passport: a funded programme, or one contracted work within it.

    Only facts taken from a named source are stored. Anything a source does not publish stays empty,
    and the passport says it is missing.
    """

    __tablename__ = "infra_projects"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_infra_project_source_external"),
        CheckConstraint(_in("level", INFRA_LEVELS), name="infra_level_valid"),
        Index("ix_infra_projects_states", "state_slugs", postgresql_using="gin"),
        Index("ix_infra_projects_search", "search", postgresql_using="gin"),
        Index("ix_infra_projects_type_awarded", "asset_type", "awarded_on"),
        Index("ix_infra_projects_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    parent_external_id: Mapped[str | None] = mapped_column(String(200))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("infra_projects.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False, default="other", server_default="other")
    state_slugs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    state_basis: Mapped[str | None] = mapped_column(String(20))  # "source", "name" or "programme"
    funder: Mapped[str | None] = mapped_column(Text)
    borrower: Mapped[str | None] = mapped_column(Text)
    implementing_agency: Mapped[str | None] = mapped_column(Text)
    tendering_body: Mapped[str | None] = mapped_column(Text)
    budget_amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    budget_currency: Mapped[str | None] = mapped_column(String(3))
    funder_amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    funder_currency: Mapped[str | None] = mapped_column(String(3))
    contract_amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    contract_currency: Mapped[str | None] = mapped_column(String(3))
    contractors_text: Mapped[str | None] = mapped_column(Text)
    procurement_method: Mapped[str | None] = mapped_column(String(100))
    bids_evaluated: Mapped[int | None] = mapped_column(Integer)
    bids_rejected: Mapped[int | None] = mapped_column(Integer)
    approved_on: Mapped[date | None] = mapped_column(Date)
    tendered_on: Mapped[date | None] = mapped_column(Date)
    bid_deadline_on: Mapped[date | None] = mapped_column(Date)
    awarded_on: Mapped[date | None] = mapped_column(Date)
    duration_text: Mapped[str | None] = mapped_column(String(50))
    promised_completion_on: Mapped[date | None] = mapped_column(Date)
    promised_basis: Mapped[str | None] = mapped_column(String(20))  # "source" or "calculated"
    actual_completion_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    tender_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}", deferred=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', left(coalesce(title, ''), 1000) || ' ' || left(coalesce(description, ''), 4000) || ' ' || "
            "left(coalesce(implementing_agency, ''), 1000) || ' ' || left(coalesce(tendering_body, ''), 500) || ' ' || "
            "left(coalesce(contractors_text, ''), 1000) || ' ' || coalesce(external_id, ''))",
            persisted=True,
        ),
        deferred=True,
    )

    source: Mapped[Source] = relationship()
    parent: Mapped[InfraProject | None] = relationship(remote_side="InfraProject.id")
    events: Mapped[list[InfraEvent]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="InfraEvent.occurred_on.asc().nulls_last()"
    )
    parties: Mapped[list[InfraProjectParty]] = relationship(back_populates="project", cascade="all, delete-orphan")


class InfraParty(Base):
    """A contractor or agency named in official records. One row per normalised name."""

    __tablename__ = "infra_parties"
    __table_args__ = (UniqueConstraint("key", "kind", name="uq_infra_party_key_kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="contractor", server_default="contractor")
    created_at: Mapped[datetime] = created_at()


class InfraProjectParty(Base):
    __tablename__ = "infra_project_parties"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("infra_projects.id", ondelete="CASCADE"), primary_key=True)
    party_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("infra_parties.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True, default="contractor")

    project: Mapped[InfraProject] = relationship(back_populates="parties")
    party: Mapped[InfraParty] = relationship()


class InfraEvent(Base):
    """One dated step in a project's history, with what kind of evidence supports it."""

    __tablename__ = "infra_events"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_infra_event_project_key"),
        CheckConstraint(_in("evidence", INFRA_EVIDENCE), name="infra_event_evidence_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("infra_projects.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_on: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(String(20), nullable=False, default="official")
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    project: Mapped[InfraProject] = relationship(back_populates="events")


class InfraDocument(Base):
    """An official document about public works that is not (yet) linked to one project, such as a CAG audit report."""

    __tablename__ = "infra_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_infra_document_source_external"),
        Index("ix_infra_documents_states", "state_slugs", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="audit_report")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    report_type: Mapped[str | None] = mapped_column(String(50))
    sectors: Mapped[str | None] = mapped_column(Text)
    state_slugs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    published_on: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()
