"""Tables for community infrastructure posts.

Kept apart on purpose: image assessments (CommunityAssessment), community verification (notes and ratings),
discussions (comments and votes) and links to official records (CommunityProjectLink). No table here is updated
from another component: a note or comment never changes an assessment or a score.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..models import Base, User, _in, created_at, uuid_pk
from .categories import CATEGORIES

CATEGORY_SLUGS = tuple(c.slug for c in CATEGORIES)
LOCALITY_KINDS = ("village", "town", "city")
POST_STATUSES = ("processing", "published", "hidden", "removed")
ANALYSIS_STATUSES = ("pending", "done", "failed")
LOCATION_SOURCES = ("pin", "photo", "device")
NOTE_KINDS = ("context", "correction", "evidence")
NOTE_STATUSES = ("pending", "helpful", "not_helpful", "disputed", "insufficient")
RATINGS = ("helpful", "somewhat", "not_helpful")
REPORT_TARGETS = ("post", "comment", "note")
REPORT_STATUSES = ("open", "actioned", "dismissed")
CONSTITUENCY_KINDS = ("lok_sabha", "assembly")
LINK_STATUSES = ("linked", "rejected")


def _fk_user(nullable: bool = True):
    return mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=nullable)


class CommunityLocality(Base):
    """A village, town or city, named by contributors and kept unique within its state and district."""

    __tablename__ = "community_localities"
    __table_args__ = (CheckConstraint(_in("kind", LOCALITY_KINDS), name="locality_kind_valid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(400), nullable=False, unique=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="IN")
    state_slug: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    district: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


class CommunityPost(Base):
    __tablename__ = "community_posts"
    __table_args__ = (
        CheckConstraint(_in("category", CATEGORY_SLUGS), name="post_category_valid"),
        CheckConstraint(_in("status", POST_STATUSES), name="post_status_valid"),
        CheckConstraint(_in("analysis_status", ANALYSIS_STATUSES), name="post_analysis_valid"),
        CheckConstraint(_in("location_source", LOCATION_SOURCES), name="post_location_source_valid"),
        CheckConstraint("latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180", name="post_coordinates_valid"),
        Index("ix_community_posts_feed", "status", "created_at"),
        Index("ix_community_posts_rank", "category", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    author_id: Mapped[uuid.UUID | None] = _fk_user()
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    location_source: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pin")
    locality_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_localities.id"), nullable=False, index=True
    )
    state_slug: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    district: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, server_default="IN")
    official_reference: Mapped[str | None] = mapped_column(String(200))  # a reference number visible on site, if any
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="processing")
    analysis_status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    analysis_error: Mapped[str | None] = mapped_column(Text)
    # Set when the same contributor shared a very similar photo of the same spot shortly before (for example a crop):
    # the post stays visible but is not counted again in rankings.
    repeat_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    share_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User | None] = relationship()
    locality: Mapped[CommunityLocality] = relationship()
    image: Mapped[CommunityImage | None] = relationship(back_populates="post", uselist=False)


class CommunityImage(Base):
    __tablename__ = "community_images"
    __table_args__ = (Index("ix_community_images_phash", "phash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    thumb_key: Mapped[str] = mapped_column(String(300), nullable=False)
    mime: Mapped[str] = mapped_column(String(40), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pixel_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    phash: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dhash: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quality: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    privacy_blurred: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # faces and plates blurred
    clip_embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL))  # normalised CLIP image vector, for repeats
    created_at: Mapped[datetime] = created_at()

    post: Mapped[CommunityPost] = relationship(back_populates="image")


class CommunityAssessment(Base):
    """What the vision models observed in one photo, and the visible-condition score derived from it.

    Written only by the assessment engine. Notes, ratings and comments never update it.
    """

    __tablename__ = "community_assessments"
    __table_args__ = (Index("uq_community_assessment_current", "post_id", unique=True, postgresql_where=text("is_current")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    category: Mapped[str] = mapped_column(String(30), nullable=False)  # the category the criteria came from
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    quality: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    scene: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    detections: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    segmentation: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    mask_key: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    description_model: Mapped[str | None] = mapped_column(String(100))
    observations: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    criteria: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    score: Mapped[float | None] = mapped_column(Numeric(5, 1))
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    confidence_label: Mapped[str | None] = mapped_column(String(10))
    not_scored_reason: Mapped[str | None] = mapped_column(Text)
    seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = created_at()


class CommunityPostEdit(Base):
    __tablename__ = "community_post_edits"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    editor_id: Mapped[uuid.UUID | None] = _fk_user()
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()

    editor: Mapped[User | None] = relationship()


# ------------------------------------------------------------------------------------------------ community notes
class CommunityNote(Base):
    __tablename__ = "community_notes"
    __table_args__ = (
        CheckConstraint(_in("kind", NOTE_KINDS), name="note_kind_valid"),
        CheckConstraint(_in("status", NOTE_STATUSES), name="note_status_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = _fk_user()
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(14), nullable=False, server_default="pending")
    intercept: Mapped[float | None] = mapped_column(Float)
    factor: Mapped[float | None] = mapped_column(Float)
    ratings_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User | None] = relationship()


class CommunityNoteVersion(Base):
    """Every earlier text of a note, so edits stay visible."""

    __tablename__ = "community_note_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class CommunityNoteRating(Base):
    __tablename__ = "community_note_ratings"
    __table_args__ = (
        UniqueConstraint("note_id", "rater_id", name="uq_note_rating_rater"),
        CheckConstraint(_in("value", RATINGS), name="note_rating_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rater_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    value: Mapped[str] = mapped_column(String(12), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityNoteStatusEvent(Base):
    """Each change of a note's status, with the numbers that decided it."""

    __tablename__ = "community_note_status_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(14), nullable=False)
    intercept: Mapped[float | None] = mapped_column(Float)
    factor: Mapped[float | None] = mapped_column(Float)
    ratings_count: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = created_at()


# ------------------------------------------------------------------------------------------------ discussions
class CommunityComment(Base):
    __tablename__ = "community_comments"

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_comments.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = _fk_user()
    body: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = created_at()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    author: Mapped[User | None] = relationship()


class CommunityCommentVote(Base):
    __tablename__ = "community_comment_votes"
    __table_args__ = (CheckConstraint("value IN (-1, 1)", name="comment_vote_valid"),)

    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_comments.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class CommunityReport(Base):
    __tablename__ = "community_reports"
    __table_args__ = (
        CheckConstraint(_in("target_type", REPORT_TARGETS), name="report_target_valid"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="community_report_status_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    reporter_id: Mapped[uuid.UUID | None] = _fk_user()
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")
    created_at: Mapped[datetime] = created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------------------------------------ representatives
class Constituency(Base):
    __tablename__ = "community_constituencies"
    __table_args__ = (
        UniqueConstraint("kind", "state_slug", "number", name="uq_constituency_kind_state_number"),
        CheckConstraint(_in("kind", CONSTITUENCY_KINDS), name="constituency_kind_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    state_slug: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_number: Mapped[int | None] = mapped_column(Integer)  # for an assembly seat: its Lok Sabha constituency
    geometry: Mapped[dict] = mapped_column(JSONB, nullable=False)
    min_lng: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lng: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    boundary_status: Mapped[str] = mapped_column(String(60), nullable=False, server_default="")  # e.g. "Pre delimitation"
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


class Representative(Base):
    __tablename__ = "community_representatives"

    id: Mapped[uuid.UUID] = uuid_pk()
    constituency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_constituencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # MP or MLA
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    party: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    term_start: Mapped[date | None] = mapped_column(Date)
    term_end: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()

    constituency: Mapped[Constituency] = relationship()


class PostRepresentative(Base):
    __tablename__ = "community_post_representatives"

    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), primary_key=True
    )
    constituency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_constituencies.id", ondelete="CASCADE"), primary_key=True
    )
    representative_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_representatives.id", ondelete="SET NULL")
    )
    computed_at: Mapped[datetime] = created_at()

    constituency: Mapped[Constituency] = relationship()
    representative: Mapped[Representative | None] = relationship()


# ------------------------------------------------------------------------------------------------ official projects
class CommunityProjectLink(Base):
    """A community post linked to an official project, only on evidence (see community/projects.py)."""

    __tablename__ = "community_project_links"
    __table_args__ = (
        UniqueConstraint("post_id", "project_id", name="uq_community_project_link"),
        CheckConstraint(_in("status", LINK_STATUSES), name="project_link_status_valid"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("infra_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="linked")
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = _fk_user()
    created_at: Mapped[datetime] = created_at()
