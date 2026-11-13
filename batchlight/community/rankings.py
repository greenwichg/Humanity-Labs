"""Category-by-category rankings of visible-condition scores at village, town, city, state and country level.

Rules, shown on the rankings page:
- One category at a time. Different categories are never averaged together, at any level.
- A score enters only if its photo was usable and the assessment produced a score. Community notes never change it.
- A place is ranked only with at least MIN_RANK observations from at least MIN_CONTRIBUTORS different contributors;
  otherwise it is listed as "too few to rank", with its count.
- Every average shows its number of observations, a 95% margin of error and the average assessment confidence.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..infra.geo import BY_SLUG
from .models import CommunityAssessment, CommunityLocality, CommunityPost

LEVELS = ("village", "town", "city", "state", "country")
LEVEL_LABELS = {"village": "Villages", "town": "Towns", "city": "Cities", "state": "States and UTs", "country": "Countries"}
LEVEL_SINGULAR = {"village": "Village", "town": "Town", "city": "City", "state": "State or UT", "country": "Country"}
COUNTRIES = {"IN": "India"}
MIN_RANK = 5
MIN_CONTRIBUTORS = 2
TREND_MONTHS = 12


@dataclass
class Unit:
    key: str
    name: str
    level: str
    n: int
    contributors: int
    mean: float
    margin: float | None  # 95% margin of error, or None with fewer than two observations
    confidence: float
    first: date
    last: date
    rank: int | None = None
    trend: list[tuple[str, float, int]] = field(default_factory=list)  # (YYYY-MM, mean, n)
    state: str | None = None

    @property
    def coverage(self) -> str:
        if self.n < MIN_RANK or self.contributors < MIN_CONTRIBUTORS:
            return "Too few to rank"
        if self.n < 15:
            return "Limited"
        if self.n < 50:
            return "Moderate"
        return "Good"

    @property
    def ranked(self) -> bool:
        return self.rank is not None

    @property
    def why_not_ranked(self) -> str | None:
        if self.n < MIN_RANK:
            return f"{self.n} of {MIN_RANK} observations needed"
        if self.contributors < MIN_CONTRIBUTORS:
            return f"all from one contributor; {MIN_CONTRIBUTORS} needed"
        return None


def _scored(category: str):
    """Published posts of one category with a current, scored assessment."""
    return (
        select(CommunityPost, CommunityAssessment.score, CommunityAssessment.confidence, CommunityAssessment.created_at)
        .join(
            CommunityAssessment, and_(CommunityAssessment.post_id == CommunityPost.id, CommunityAssessment.is_current.is_(True))
        )
        .where(
            CommunityPost.category == category,
            CommunityPost.status == "published",
            CommunityPost.repeat_of.is_(None),  # a repeat of the same spot by the same person counts once
            CommunityAssessment.score.is_not(None),
        )
    )


def _group_key(level: str):
    if level in ("village", "town", "city"):
        return CommunityPost.locality_id
    if level == "state":
        return CommunityPost.state_slug
    return CommunityPost.country_code


def units(db: Session, category: str, level: str, state: str | None = None, keys: list[str] | None = None) -> list[Unit]:
    if level not in LEVELS:
        raise ValueError(level)
    key = _group_key(level)
    base = _scored(category).subquery()
    p = base.c
    gk = {
        "village": p.locality_id,
        "town": p.locality_id,
        "city": p.locality_id,
        "state": p.state_slug,
        "country": p.country_code,
    }[level]
    q = select(
        gk.label("k"),
        func.count().label("n"),
        func.count(func.distinct(p.author_id)).label("contributors"),
        func.avg(p.score).label("mean"),
        func.stddev_samp(p.score).label("sd"),
        func.avg(p.confidence).label("conf"),
        func.min(p.created_at).label("first"),
        func.max(p.created_at).label("last"),
    ).group_by(gk)
    if level in ("village", "town", "city"):
        q = q.join(CommunityLocality, CommunityLocality.id == p.locality_id).where(CommunityLocality.kind == level)
        if state:
            q = q.where(p.state_slug == state)
    elif level == "state" and state:
        q = q.where(p.state_slug == state)
    if keys:
        q = q.where(gk.in_([uuid.UUID(k) if level in ("village", "town", "city") else k for k in keys]))
    rows = db.execute(q).all()
    names = _names(db, level, [r.k for r in rows])
    out = []
    for r in rows:
        n = int(r.n)
        margin = round(1.96 * float(r.sd) / math.sqrt(n), 1) if n >= 2 and r.sd is not None else None
        name, st = names.get(r.k, (str(r.k), None))
        out.append(
            Unit(
                str(r.k),
                name,
                level,
                n,
                int(r.contributors),
                round(float(r.mean), 1),
                margin,
                round(float(r.conf or 0), 2),
                r.first.date(),
                r.last.date(),
                state=st,
            )
        )
    ranked = sorted(
        (u for u in out if u.n >= MIN_RANK and u.contributors >= MIN_CONTRIBUTORS), key=lambda u: (-u.mean, -u.n, u.name)
    )
    for i, u in enumerate(ranked, 1):
        u.rank = i
    rest = sorted((u for u in out if u.rank is None), key=lambda u: (-u.n, u.name))
    result = ranked + rest
    _attach_trends(db, category, level, result, key)
    return result


def _names(db: Session, level: str, keys: list) -> dict:
    if level in ("village", "town", "city"):
        locs = db.scalars(select(CommunityLocality).where(CommunityLocality.id.in_(keys))).all() if keys else []
        return {loc.id: (f"{loc.name}{', ' + loc.district if loc.district else ''}", loc.state_slug) for loc in locs}
    if level == "state":
        return {k: (BY_SLUG[k].name if k in BY_SLUG else k, k) for k in keys}
    return {k: (COUNTRIES.get(k, k), None) for k in keys}


def _attach_trends(db: Session, category: str, level: str, items: list[Unit], key) -> None:
    if not items:
        return
    base = _scored(category).subquery()
    p = base.c
    gk = {
        "village": p.locality_id,
        "town": p.locality_id,
        "city": p.locality_id,
        "state": p.state_slug,
        "country": p.country_code,
    }[level]
    month = func.to_char(func.date_trunc("month", p.created_at), "YYYY-MM")
    since = datetime.now(UTC).replace(day=1)
    since = since.replace(year=since.year - 1)
    q = (
        select(gk.label("k"), month.label("m"), func.avg(p.score), func.count())
        .where(p.created_at >= since)
        .group_by(gk, month)
        .order_by(month)
    )
    by: dict[str, list] = {}
    for k, m, avg, n in db.execute(q).all():
        by.setdefault(str(k), []).append((m, round(float(avg), 1), int(n)))
    for u in items:
        u.trend = by.get(u.key, [])


def sparkline(trend: list[tuple[str, float, int]], width: int = 120, height: int = 28) -> str:
    """Inline SVG path for a 0-100 score trend; empty with fewer than two months."""
    if len(trend) < 2:
        return ""
    step = width / (len(trend) - 1)
    pts = [f"{i * step:.1f},{height - (v / 100) * height:.1f}" for i, (_, v, _) in enumerate(trend)]
    return "M" + " L".join(pts)


def unit_label(level: str, key: str, db: Session) -> tuple[str, str | None]:
    return _names(db, level, [uuid.UUID(key) if level in ("village", "town", "city") else key]).get(
        uuid.UUID(key) if level in ("village", "town", "city") else key, (key, None)
    )
