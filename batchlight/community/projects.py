"""Link community posts to official project records from Infrastructure Transparency, only on evidence.

A post is linked to a project only when all three hold:
  1. the reference number the contributor read on site (a tender reference, a PMGSY package number or a project
     code, as printed on citizen information boards) matches the official record exactly, ignoring spacing and
     punctuation;
  2. the project's recorded state includes the post's state;
  3. the type of work fits the post's category.
Similar names or nearby places are never enough: they are shown separately as "might be related", unlinked.

Financial figures shown with a link are the official record's, for the whole project or contract, with their source.
They are never presented as the cost of the section in the photo.
"""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..infra import passport
from ..infra.geo import BY_SLUG
from ..models import InfraProject
from . import categories as C
from .models import CommunityPost, CommunityProjectLink

FINANCE_WORDS = re.compile(r"cost|price|budget|spent|fund|carries|contractor|tender|sanction", re.IGNORECASE)
CAVEAT = (
    "These figures are for the whole project or contract in the official record. They are not the cost of the "
    "stretch or building in this photo, and they say nothing about its condition."
)


def normalise(ref: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


def candidates_by_reference(db: Session, reference: str) -> list[InfraProject]:
    ref = normalise(reference)
    if len(ref) < 5:
        return []
    return list(
        db.scalars(
            select(InfraProject).where(
                text(
                    "regexp_replace(upper(infra_projects.external_id), '[^A-Z0-9]', '', 'g') = :r"
                    " OR regexp_replace(upper(coalesce(infra_projects.data->>'package', '')), '[^A-Z0-9]', '', 'g') = :r"
                    " OR regexp_replace(upper(coalesce(infra_projects.data->>'project_code', '')), '[^A-Z0-9]', '', 'g') = :r"
                ).bindparams(r=ref)
            )
        ).all()
    )


def evidence_for(post: CommunityPost, project: InfraProject) -> tuple[bool, list[str]]:
    """Whether the evidence is enough to link, and the reasons, each shown to people."""
    cat = C.get(post.category)
    reasons, ok = [], True
    reasons.append(f"The reference on the post ({post.official_reference}) matches the official record.")
    if project.state_slugs and post.state_slug not in project.state_slugs:
        ok = False
        reasons.append(
            "But the official record is for "
            + ", ".join(BY_SLUG[s].name for s in project.state_slugs if s in BY_SLUG)
            + ", not this post's state."
        )
    elif project.state_slugs:
        reasons.append(f"Same state: {BY_SLUG[post.state_slug].name}.")
    else:
        ok = False
        reasons.append("But the official record names no state, so the place cannot be confirmed.")
    if project.asset_type in cat.infra_types:
        reasons.append(f"The type of work fits: {project.asset_type.replace('_', ' ')}.")
    else:
        ok = False
        reasons.append(f"But the official record is for {project.asset_type.replace('_', ' ')} work, not {cat.label.lower()}.")
    return ok, reasons


def link(db: Session, post: CommunityPost, user_id=None) -> list[CommunityProjectLink]:
    """(Re)evaluate the post's reference. Only fully evidenced matches are stored as links."""
    db.query(CommunityProjectLink).filter(
        CommunityProjectLink.post_id == post.id, CommunityProjectLink.method == "reference_match"
    ).delete()
    if not post.official_reference:
        return []
    made = []
    for project in candidates_by_reference(db, post.official_reference)[:5]:
        ok, reasons = evidence_for(post, project)
        row = CommunityProjectLink(
            post_id=post.id,
            project_id=project.id,
            status="linked" if ok else "rejected",
            method="reference_match",
            evidence=reasons,
            created_by=user_id,
        )
        db.add(row)
        made.append(row)
    db.flush()
    return made


def might_be_related(db: Session, post: CommunityPost, limit: int = 3) -> list[InfraProject]:
    """Official records in the same state and of a fitting type whose names share words with the post. Never linked."""
    words = [
        w
        for w in re.findall(r"[a-z]{4,}", f"{post.title} {post.locality.name if post.locality else ''} {post.district}".lower())
        if w not in STOP
    ]
    if not words:
        return []
    cat = C.get(post.category)
    q = " | ".join(sorted(set(words))[:12])
    return list(
        db.scalars(
            select(InfraProject)
            .where(
                InfraProject.state_slugs.any(post.state_slug),
                InfraProject.asset_type.in_(cat.infra_types),
                InfraProject.search.op("@@")(func.to_tsquery("simple", q)),
            )
            .order_by(func.ts_rank(InfraProject.search, func.to_tsquery("simple", q)).desc())
            .limit(limit)
        ).all()
    )


STOP = {
    "road",
    "roads",
    "near",
    "with",
    "from",
    "this",
    "that",
    "road's",
    "photo",
    "street",
    "there",
    "have",
    "been",
    "school",
    "bridge",
    "village",
    "town",
    "city",
    "district",
    "main",
}


def finance_facts(db: Session, project: InfraProject) -> list[passport.Fact]:
    parent = db.get(InfraProject, project.parent_id) if project.parent_id else None
    return [f for f in passport.facts(project, parent, date.today()) if FINANCE_WORDS.search(f.question)]


def links_for(db: Session, post_id) -> list[CommunityProjectLink]:
    return list(
        db.scalars(
            select(CommunityProjectLink).where(CommunityProjectLink.post_id == post_id).order_by(CommunityProjectLink.status)
        ).all()
    )
