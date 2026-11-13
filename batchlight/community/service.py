"""Creating and editing community posts. The photo checks here are fast; the vision models run later in the worker."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..infra.geo import BY_SLUG
from . import categories as C
from .duplicates import find_duplicate
from .models import LOCALITY_KINDS, CommunityImage, CommunityLocality, CommunityPost, CommunityPostEdit
from .storage import keys, storage
from .vision.image import ImageRejected, prepare, thumbnail

# India's bounding box, generously: posts outside it are refused until other countries are supported.
INDIA_BOUNDS = (6.0, 68.0, 37.5, 97.6)


class PostRejected(ValueError):
    def __init__(self, message: str, field: str = "form", duplicate_of: uuid.UUID | None = None):
        super().__init__(message)
        self.field = field
        self.duplicate_of = duplicate_of


@dataclass
class PostInput:
    category: str
    title: str
    body: str
    latitude: float | None
    longitude: float | None
    location_source: str
    state_slug: str
    district: str
    locality_kind: str
    locality_name: str
    official_reference: str


def _clean(s: str, limit: int) -> str:
    return " ".join((s or "").split())[:limit]


def locality_key(country: str, state: str, district: str, kind: str, name: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    return "|".join([country, state, norm(district), kind, norm(name)])


def get_or_create_locality(
    db: Session, state: str, district: str, kind: str, name: str, country: str = "IN"
) -> CommunityLocality:
    key = locality_key(country, state, district, kind, name)
    loc = db.scalar(select(CommunityLocality).where(CommunityLocality.key == key))
    if loc is None:
        loc = CommunityLocality(
            key=key,
            country_code=country,
            state_slug=state,
            district=_clean(district, 120).title(),
            kind=kind,
            name=_clean(name, 200).title(),
        )
        db.add(loc)
        db.flush()
    return loc


def validate(data: PostInput) -> dict[str, str]:
    errors = {}
    if data.category not in C.BY_SLUG:
        errors["category"] = "Choose what the photo shows."
    if not 8 <= len(_clean(data.title, 300)) <= 200:
        errors["title"] = "Give a short title of 8 to 200 characters, such as 'Potholes on Station Road near the bus stand'."
    if len(data.body or "") > 4000:
        errors["body"] = "Please keep the description under 4,000 characters."
    if data.latitude is None or data.longitude is None:
        errors["location"] = "Mark where the photo was taken: use your location or enter the coordinates."
    else:
        s, w, n, e = INDIA_BOUNDS
        if not (s <= data.latitude <= n and w <= data.longitude <= e):
            errors["location"] = "That position is outside India. Only places in India can be posted for now."
    if data.state_slug not in BY_SLUG:
        errors["state_slug"] = "Choose the state or union territory."
    if data.locality_kind not in LOCALITY_KINDS:
        errors["locality_kind"] = "Say whether the place is a village, town or city."
    if not 2 <= len(_clean(data.locality_name, 300)) <= 200:
        errors["locality_name"] = "Name the village, town or city."
    if len(data.official_reference or "") > 200:
        errors["official_reference"] = "The reference number is too long."
    return errors


def daily_posts(db: Session, user_id) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    return (
        db.scalar(
            select(func.count())
            .select_from(CommunityPost)
            .where(CommunityPost.author_id == user_id, CommunityPost.created_at >= since)
        )
        or 0
    )


def create_post(db: Session, user_id, data: PostInput, raw: bytes) -> CommunityPost:
    """Validate, check the photo, refuse duplicates, store it, and create the post in 'processing' state."""
    if daily_posts(db, user_id) >= get_settings().community_posts_per_day:
        raise PostRejected("You have shared the most photos allowed in one day. Please try again tomorrow.")
    try:
        prepared = prepare(raw)
    except ImageRejected as exc:
        raise PostRejected(str(exc), "photo") from None
    dup = find_duplicate(db, prepared.sha256, prepared.pixel_sha256, prepared.phash, prepared.dhash)
    if dup:
        post_id, how = dup
        raise PostRejected(
            "This photo has already been shared"
            + (" (an almost identical copy)" if how == "near" else "")
            + ". Add to that post's discussion or notes instead.",
            "photo",
            duplicate_of=post_id,
        )
    loc = get_or_create_locality(db, data.state_slug, data.district, data.locality_kind, data.locality_name)
    post = CommunityPost(
        author_id=user_id,
        category=data.category,
        title=_clean(data.title, 200),
        body=(data.body or "").strip()[:4000],
        latitude=round(float(data.latitude), 6),
        longitude=round(float(data.longitude), 6),
        location_source=data.location_source if data.location_source in ("pin", "photo", "device") else "pin",
        locality_id=loc.id,
        state_slug=data.state_slug,
        district=loc.district,
        official_reference=_clean(data.official_reference, 200) or None,
        status="processing",
        analysis_status="pending",
    )
    db.add(post)
    db.flush()
    k = keys(post.id)
    store = storage()
    store.put(k["photo"], prepared.data)
    store.put(k["thumb"], thumbnail(prepared.data))
    db.add(
        CommunityImage(
            post_id=post.id,
            storage_key=k["photo"],
            thumb_key=k["thumb"],
            mime=prepared.mime,
            width=prepared.width,
            height=prepared.height,
            bytes=len(prepared.data),
            sha256=prepared.sha256,
            pixel_sha256=prepared.pixel_sha256,
            phash=prepared.phash,
            dhash=prepared.dhash,
            quality=prepared.quality.as_dict(),
        )
    )
    db.flush()
    return post


EDITABLE = ("title", "body", "official_reference")


def edit_post(db: Session, post: CommunityPost, editor_id, title: str, body: str, official_reference: str) -> list[str]:
    """Apply edits, keeping every earlier value. Category, place and photo cannot change after posting."""
    new = {
        "title": _clean(title, 200),
        "body": (body or "").strip()[:4000],
        "official_reference": _clean(official_reference, 200) or None,
    }
    if not 8 <= len(new["title"]) <= 200:
        raise PostRejected("Give a short title of 8 to 200 characters.", "title")
    changed = []
    for field in EDITABLE:
        old = getattr(post, field)
        if (old or None) != (new[field] or None):
            db.add(CommunityPostEdit(post_id=post.id, editor_id=editor_id, field=field, old_value=old, new_value=new[field]))
            setattr(post, field, new[field])
            changed.append(field)
    if changed:
        post.edited_at = datetime.now(UTC)
    return changed
