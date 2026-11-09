"""Public infrastructure: Project Passports, states and union territories, contractors and source coverage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased, selectinload

from ...infra.classify import ASSET_TYPES
from ...infra.geo import BY_SLUG, REGIONS, region_name
from ...infra.passport import EVIDENCE_HELP, EVIDENCE_LABELS, facts, money
from ...infra.sources import NOT_CONNECTED, REGISTRY
from ...models import InfraDocument, InfraParty, InfraProject, InfraProjectParty, Source
from ...services.alerts import Page
from ..auth import DB, OptionalAuth
from ..templating import env, render

router = APIRouter(prefix="/infrastructure", include_in_schema=False)
PER_PAGE = 25
env.globals.update(
    INFRA_EVIDENCE_LABELS=EVIDENCE_LABELS,
    INFRA_EVIDENCE_HELP=EVIDENCE_HELP,
    ASSET_TYPES=ASSET_TYPES,
    money=money,
    region_name=region_name,
)


def _children():
    child = aliased(InfraProject)
    return select(child.id).where(child.parent_id == InfraProject.id).exists()


def visible():
    """Works, plus programmes that are infrastructure or have works. Other World Bank programmes stay hidden."""
    return or_(InfraProject.level == "work", InfraProject.asset_type != "other", _children())


def state_counts(db) -> dict[str, int]:
    slug = func.unnest(InfraProject.state_slugs).label("slug")
    sub = select(slug).where(InfraProject.level == "work").subquery()
    return dict(db.execute(select(sub.c.slug, func.count()).group_by(sub.c.slug)).all())


def search_projects(db, q: str, state: str, kind: str, level: str, contractor: uuid.UUID | None, page: int) -> Page:
    stmt = select(InfraProject).where(visible())
    if q:
        stmt = stmt.where(InfraProject.search.op("@@")(func.websearch_to_tsquery("simple", q)))
    if state:
        stmt = stmt.where(InfraProject.state_slugs.contains([state]))
    if kind:
        stmt = stmt.where(InfraProject.asset_type == kind)
    if level:
        stmt = stmt.where(InfraProject.level == level)
    if contractor:
        stmt = stmt.where(
            select(InfraProjectParty.project_id)
            .where(InfraProjectParty.project_id == InfraProject.id, InfraProjectParty.party_id == contractor)
            .exists()
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(
            func.coalesce(InfraProject.awarded_on, InfraProject.tendered_on, InfraProject.approved_on).desc().nulls_last(),
            InfraProject.title,
        )
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
    ).all()
    return Page(items, total, page, PER_PAGE)


@router.get("")
def home(request: Request, db: DB, auth: OptionalAuth):
    counts = state_counts(db)
    works = db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.level == "work")) or 0
    programmes = (
        db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.level == "programme", visible())) or 0
    )
    contractors = db.scalar(select(func.count()).select_from(InfraParty).where(InfraParty.kind == "contractor")) or 0
    recent = db.scalars(
        select(InfraProject)
        .where(InfraProject.level == "work", InfraProject.awarded_on.is_not(None))
        .order_by(InfraProject.awarded_on.desc())
        .limit(8)
    ).all()
    return render(
        request,
        "infra/home.html",
        {
            "regions": REGIONS,
            "counts": counts,
            "works": works,
            "programmes": programmes,
            "contractors": contractors,
            "covered": sum(1 for r in REGIONS if counts.get(r.slug)),
            "recent": recent,
            "types": ASSET_TYPES,
        },
    )


@router.get("/projects")
def projects(
    request: Request,
    db: DB,
    auth: OptionalAuth,
    q: str = "",
    state: str = "",
    type: str = "",
    level: str = "",
    contractor: str = "",
    page: int = Query(1, ge=1, le=10_000),
):
    q = q.strip()[:200]
    state = state if state in BY_SLUG else ""
    type = type if type in ASSET_TYPES else ""
    level = level if level in ("work", "programme") else ""
    try:
        contractor_id = uuid.UUID(contractor) if contractor else None
    except ValueError:
        contractor_id = None
    party = db.get(InfraParty, contractor_id) if contractor_id else None
    result = search_projects(db, q, state, type, level, party.id if party else None, page)
    return render(
        request,
        "infra/projects.html",
        {
            "page": result,
            "q": q,
            "state": state,
            "type": type,
            "level": level,
            "party": party,
            "regions": REGIONS,
            "types": ASSET_TYPES,
        },
    )


@router.get("/projects/{project_id}")
def project(request: Request, project_id: uuid.UUID, db: DB, auth: OptionalAuth):
    p = db.scalar(
        select(InfraProject)
        .where(InfraProject.id == project_id)
        .options(selectinload(InfraProject.events), selectinload(InfraProject.parties).selectinload(InfraProjectParty.party))
    )
    if p is None:
        raise HTTPException(404)
    parent = db.get(InfraProject, p.parent_id) if p.parent_id else None
    works, works_total = [], 0
    if p.level == "programme":
        works_total = db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.parent_id == p.id)) or 0
        works = db.scalars(
            select(InfraProject)
            .where(InfraProject.parent_id == p.id)
            .order_by(func.coalesce(InfraProject.awarded_on, InfraProject.tendered_on).desc().nulls_last())
            .limit(50)
        ).all()
    events = list(p.events)
    if parent is not None:
        events = [e for e in parent.events if e.kind in ("approval", "funding")] + events
    events.sort(key=lambda e: (e.occurred_on is None, e.occurred_on or datetime.min.date()))
    rows = facts(p, parent, datetime.now(UTC).date())
    audits = []
    if p.state_slugs:
        audits = db.scalars(
            select(InfraDocument)
            .where(InfraDocument.state_slugs.overlap(p.state_slugs))
            .order_by(InfraDocument.published_on.desc().nulls_last())
            .limit(5)
        ).all()
    return render(
        request,
        "infra/project.html",
        {
            "p": p,
            "parent": parent,
            "facts": rows,
            "known": [f for f in rows if not f.missing],
            "missing": [f for f in rows if f.missing],
            "events": events,
            "works": works,
            "works_total": works_total,
            "contractors": [pp.party for pp in p.parties if pp.role == "contractor"],
            "audits": audits,
            "source": db.get(Source, p.source_id),
        },
    )


@router.get("/states/{slug}")
def state_page(request: Request, slug: str, db: DB, auth: OptionalAuth):
    region = BY_SLUG.get(slug)
    if region is None:
        raise HTTPException(404)
    in_state = InfraProject.state_slugs.contains([slug])
    works = db.scalar(select(func.count()).select_from(InfraProject).where(in_state, InfraProject.level == "work")) or 0
    by_type = db.execute(
        select(InfraProject.asset_type, func.count())
        .where(in_state, InfraProject.level == "work")
        .group_by(InfraProject.asset_type)
        .order_by(func.count().desc())
    ).all()
    programmes = db.scalars(
        select(InfraProject)
        .where(in_state, InfraProject.level == "programme", visible())
        .order_by(InfraProject.approved_on.desc().nulls_last())
        .limit(20)
    ).all()
    recent = db.scalars(
        select(InfraProject)
        .where(in_state, InfraProject.level == "work")
        .order_by(func.coalesce(InfraProject.awarded_on, InfraProject.tendered_on).desc().nulls_last())
        .limit(15)
    ).all()
    audits = db.scalars(
        select(InfraDocument)
        .where(InfraDocument.state_slugs.contains([slug]))
        .order_by(InfraDocument.published_on.desc().nulls_last())
        .limit(10)
    ).all()
    return render(
        request,
        "infra/state.html",
        {
            "region": region,
            "works": works,
            "by_type": by_type,
            "programmes": programmes,
            "recent": recent,
            "audits": audits,
            "pending": NOT_CONNECTED,
        },
    )


@router.get("/contractors/{party_id}")
def contractor(request: Request, party_id: uuid.UUID, db: DB, auth: OptionalAuth):
    party = db.get(InfraParty, party_id)
    if party is None:
        raise HTTPException(404)
    linked = select(InfraProjectParty.project_id).where(InfraProjectParty.party_id == party.id)
    rows = db.scalars(
        select(InfraProject).where(InfraProject.id.in_(linked)).order_by(InfraProject.awarded_on.desc().nulls_last()).limit(200)
    ).all()
    totals: dict[str, float] = {}
    for r in rows:
        if r.contract_amount is not None and r.contract_currency:
            totals[r.contract_currency] = totals.get(r.contract_currency, 0) + float(r.contract_amount)
    states = sorted({s for r in rows for s in r.state_slugs})
    return render(
        request,
        "infra/contractor.html",
        {"party": party, "rows": rows, "totals": totals, "states": states},
    )


@router.get("/sources")
def sources(request: Request, db: DB, auth: OptionalAuth):
    connected = []
    for adapter in REGISTRY.values():
        source = db.get(Source, adapter.id)
        if adapter.id == "infra-cag-reports":
            count = db.scalar(select(func.count()).select_from(InfraDocument).where(InfraDocument.source_id == adapter.id)) or 0
        else:
            count = db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.source_id == adapter.id)) or 0
        connected.append({"adapter": adapter, "source": source, "count": count})
    return render(request, "infra/sources.html", {"connected": connected, "pending": NOT_CONNECTED})
