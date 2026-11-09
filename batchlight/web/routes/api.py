"""Versioned JSON API. Public read endpoints, and workspace endpoints authenticated with API keys."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ...db import get_db
from ...models import ALERT_TYPES, OUTCOMES, SEVERITIES, Alert, ApiKey, Holding, Match
from ...security import constant_time_equals, parse_api_key, token_hash
from ...services import audit
from ...services.alerts import check_batch, first_lots, manufacturer_alerts, search_alerts, sources_status
from ...services.holdings import HoldingInput, archive_holding, create_holding, import_rows, update_holding

router = APIRouter(prefix="/api/v1")
LIST_BATCHES = 100  # batches included per alert in list responses; GET /alerts/{id} returns all
DB = Annotated[Session, Depends(get_db)]


# --------------------------------------------------------------------------- schemas


class LotOut(BaseModel):
    batch: str
    expiry: str | None


class AlertOut(BaseModel):
    id: uuid.UUID
    category: str
    source: str
    regulator: str
    country_code: str | None
    alert_type: str
    severity: str
    classification: str | None
    title: str
    product_names: list[str]
    active_ingredients: list[str]
    manufacturer: str | None
    product_codes: list[str]
    reason: str | None
    batches: list[LotOut]
    batch_count: int
    all_batches: bool
    published_at: date | None
    source_url: str
    url: str

    @classmethod
    def of(cls, alert: Alert, lots: list[tuple[str, str | None]]) -> AlertOut:
        return cls(
            id=alert.id,
            category=alert.category,
            source=alert.source_id,
            regulator=alert.regulator,
            country_code=alert.country_code,
            alert_type=alert.alert_type,
            severity=alert.severity,
            classification=alert.classification,
            title=alert.title,
            product_names=alert.product_names,
            active_ingredients=alert.active_ingredients,
            manufacturer=alert.manufacturer,
            product_codes=alert.product_codes,
            reason=alert.reason,
            batches=[LotOut(batch=lot, expiry=expiry) for lot, expiry in lots],
            batch_count=alert.lot_count,
            all_batches=alert.all_lots,
            published_at=alert.published_at,
            source_url=alert.source_url,
            url=f"/alerts/{alert.id}",
        )


class AlertPage(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[AlertOut]


class SourceOut(BaseModel):
    id: str
    name: str
    regulator: str
    country_code: str | None
    alerts: int
    last_success_at: datetime | None
    automated: bool


class CheckHit(BaseModel):
    alert: AlertOut
    batch_listed: bool
    level: Literal["batch", "product"] | None
    confidence: int | None
    reasons: list[str]


class CheckOut(BaseModel):
    batch: str
    product: str | None
    results: list[CheckHit]
    sources_searched: list[SourceOut]
    note: str = (
        "No alert is not proof that a product is genuine or of good quality. "
        "Batchlight relays official alerts and does not give medical advice."
    )


class HoldingOut(BaseModel):
    id: uuid.UUID
    product_name: str
    active_ingredient: str | None
    strength: str | None
    dosage_form: str | None
    manufacturer: str | None
    product_code: str | None
    batch: str | None
    expiry_date: date | None
    quantity: int | None
    location: str | None
    notes: str | None
    created_at: datetime

    @classmethod
    def of(cls, h: Holding) -> HoldingOut:
        return cls(
            id=h.id,
            product_name=h.product_name,
            active_ingredient=h.active_ingredient,
            strength=h.strength,
            dosage_form=h.dosage_form,
            manufacturer=h.manufacturer,
            product_code=h.product_code,
            batch=h.lot,
            expiry_date=h.expiry_date,
            quantity=h.quantity,
            location=h.location,
            notes=h.notes,
            created_at=h.created_at,
        )


class HoldingIn(BaseModel):
    product_name: str = Field(min_length=2, max_length=300)
    active_ingredient: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    manufacturer: str | None = None
    product_code: str | None = None
    batch: str | None = None
    expiry_date: str | None = Field(default=None, description="2027-04-30, 30/04/2027 or 04/2027")
    quantity: int | None = Field(default=None, ge=0)
    location: str | None = None
    notes: str | None = None

    def to_input(self) -> HoldingInput:
        data = self.model_dump()
        data["lot"] = data.pop("batch")
        return HoldingInput(**data)


class HoldingCreated(BaseModel):
    holding: HoldingOut
    new_matches: list[uuid.UUID]


class BulkIn(BaseModel):
    items: list[HoldingIn] = Field(min_length=1, max_length=1000)


class BulkOut(BaseModel):
    created: int
    duplicates: int
    new_matches: int


class MatchOut(BaseModel):
    id: uuid.UUID
    status: str
    level: str
    confidence: int
    reasons: list[str]
    outcome: str | None
    units_affected: int | None
    created_at: datetime
    resolved_at: datetime | None
    holding: HoldingOut
    alert: AlertOut

    @classmethod
    def of(cls, m: Match, lots: list[tuple[str, str | None]]) -> MatchOut:
        return cls(
            id=m.id,
            status=m.status,
            level=m.level,
            confidence=m.confidence,
            reasons=m.reasons,
            outcome=m.outcome,
            units_affected=m.units_affected,
            created_at=m.created_at,
            resolved_at=m.resolved_at,
            holding=HoldingOut.of(m.holding),
            alert=AlertOut.of(m.alert, lots),
        )


class ResolveIn(BaseModel):
    outcome: Literal[OUTCOMES]  # type: ignore[valid-type]
    units_affected: int | None = Field(default=None, ge=0, le=1_000_000_000)
    note: str | None = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------- auth


def api_org(db: DB, authorization: Annotated[str | None, Header()] = None) -> ApiKey:
    """Resolve the workspace from `Authorization: Bearer bl_<prefix>_<secret>`."""
    unauthorized = HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized
    token = authorization.split(" ", 1)[1].strip()
    prefix = parse_api_key(token)
    if prefix is None:
        raise unauthorized
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None or key.revoked_at is not None or not constant_time_equals(key.key_hash, token_hash(token)):
        raise unauthorized
    now = datetime.now(UTC)
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=5):
        key.last_used_at = now
        db.commit()
    return key


ApiKeyAuth = Annotated[ApiKey, Depends(api_org)]


# --------------------------------------------------------------------------- public endpoints


@router.get("/alerts", response_model=AlertPage, tags=["alerts"])
def list_alerts(
    db: DB,
    q: str = "",
    source: str | None = None,
    severity: Literal[SEVERITIES] | None = None,  # type: ignore[valid-type]
    alert_type: Literal[ALERT_TYPES] | None = None,  # type: ignore[valid-type]
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
    category: Literal["medicine", "food"] = "medicine",
    country: Annotated[str | None, Query(pattern=r"^[A-Z]{2}$")] = None,
):
    """Search alerts by text, batch number or (for food) barcode, newest first. Medicine alerts by default."""
    result = search_alerts(db, q, source, severity, alert_type, page, per_page, category=category, country=country)
    lots = first_lots(db, [a.id for a in result.items], LIST_BATCHES)
    return AlertPage(
        total=result.total, page=result.page, per_page=result.per_page, items=[AlertOut.of(a, lots[a.id]) for a in result.items]
    )


@router.get("/alerts/{alert_id}", response_model=AlertOut, tags=["alerts"])
def get_alert(alert_id: uuid.UUID, db: DB):
    """One alert with every batch it lists."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return AlertOut.of(alert, first_lots(db, [alert.id], 10_000)[alert.id])


def _sources(db) -> list[SourceOut]:
    return [
        SourceOut(
            id=s["source"].id,
            name=s["source"].name,
            regulator=s["source"].regulator,
            country_code=s["source"].country_code,
            alerts=s["alerts"],
            last_success_at=s["source"].last_success_at,
            automated=s["source"].automated,
        )
        for s in sources_status(db)
    ]


@router.get("/check", response_model=CheckOut, tags=["alerts"])
def check(
    db: DB,
    batch: Annotated[str, Query(max_length=100)] = "",
    product: Annotated[str | None, Query(max_length=200)] = None,
    manufacturer: Annotated[str | None, Query(max_length=200)] = None,
    category: Literal["medicine", "food"] = "medicine",
):
    """Check a batch number, optionally with product name and manufacturer, against every source."""
    if not batch.strip() and not (product or "").strip():
        raise HTTPException(422, "Provide a batch number, a product name, or both")
    hits = check_batch(db, batch, product, manufacturer, category=category)
    lots = first_lots(db, [h.alert.id for h in hits], LIST_BATCHES)
    return CheckOut(
        batch=batch,
        product=product,
        sources_searched=_sources(db),
        results=[
            CheckHit(
                alert=AlertOut.of(h.alert, lots[h.alert.id]),
                batch_listed=h.batch_match,
                level=h.decision.level if h.decision else None,
                confidence=h.decision.confidence if h.decision else None,
                reasons=h.decision.reasons if h.decision else [],
            )
            for h in hits
        ],
    )


@router.get("/manufacturers/{slug}", response_model=list[AlertOut], tags=["alerts"])
def manufacturer(slug: str, db: DB):
    """Every alert naming a manufacturer, across regulators. The slug comes from /manufacturers pages."""
    _, items = manufacturer_alerts(db, slug)
    if not items:
        raise HTTPException(404, "Manufacturer not found")
    lots = first_lots(db, [a.id for a in items], LIST_BATCHES)
    return [AlertOut.of(a, lots[a.id]) for a in items]


@router.get("/sources", response_model=list[SourceOut], tags=["alerts"])
def sources(db: DB):
    """Regulator sources and when each was last refreshed."""
    return _sources(db)


# --------------------------------------------------------------------------- workspace endpoints


def _own_holding(db, key: ApiKey, holding_id: uuid.UUID) -> Holding:
    holding = db.scalar(
        select(Holding).where(Holding.id == holding_id, Holding.org_id == key.org_id, Holding.archived_at.is_(None))
    )
    if holding is None:
        raise HTTPException(404, "Holding not found")
    return holding


def _validated(item: HoldingIn) -> HoldingInput:
    try:
        return item.to_input()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/holdings", response_model=list[HoldingOut], tags=["workspace"])
def list_holdings(
    db: DB, key: ApiKeyAuth, page: Annotated[int, Query(ge=1)] = 1, per_page: Annotated[int, Query(ge=1, le=500)] = 100
):
    rows = db.scalars(
        select(Holding)
        .where(Holding.org_id == key.org_id, Holding.archived_at.is_(None))
        .order_by(Holding.created_at)
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return [HoldingOut.of(h) for h in rows]


@router.post("/holdings", response_model=HoldingCreated, status_code=201, tags=["workspace"])
def add_holding(item: HoldingIn, db: DB, key: ApiKeyAuth):
    holding, new_matches = create_holding(db, key.org_id, _validated(item), None)
    audit.record(db, "holding_created", org_id=key.org_id, entity_type="holding", entity_id=holding.id, via="api", key=key.prefix)
    db.commit()
    return HoldingCreated(holding=HoldingOut.of(holding), new_matches=new_matches)


@router.post("/holdings/bulk", response_model=BulkOut, status_code=201, tags=["workspace"])
def add_holdings(body: BulkIn, db: DB, key: ApiKeyAuth):
    rows = []
    for index, item in enumerate(body.items):
        try:
            rows.append(item.to_input())
        except ValueError as exc:
            raise HTTPException(422, f"items[{index}]: {exc}") from None
    report = import_rows(db, key.org_id, rows, None)
    audit.record(db, "holdings_imported", org_id=key.org_id, via="api", key=key.prefix, created=report.created)
    db.commit()
    return BulkOut(created=report.created, duplicates=report.duplicates, new_matches=report.matches)


class HoldingPatch(BaseModel):
    """Fields to change. Omitted fields keep their value; send null to clear an optional field."""

    product_name: str | None = Field(default=None, min_length=2, max_length=300)
    active_ingredient: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    manufacturer: str | None = None
    product_code: str | None = None
    batch: str | None = None
    expiry_date: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    location: str | None = None
    notes: str | None = None


@router.patch("/holdings/{holding_id}", response_model=HoldingCreated, tags=["workspace"])
def edit_holding(holding_id: uuid.UUID, item: HoldingPatch, db: DB, key: ApiKeyAuth):
    """Change some fields of a holding. Editing the batch re-checks it against every alert."""
    holding = _own_holding(db, key, holding_id)
    current = HoldingIn(
        product_name=holding.product_name,
        active_ingredient=holding.active_ingredient,
        strength=holding.strength,
        dosage_form=holding.dosage_form,
        manufacturer=holding.manufacturer,
        product_code=holding.product_code,
        batch=holding.lot,
        expiry_date=holding.expiry_date.isoformat() if holding.expiry_date else None,
        quantity=holding.quantity,
        location=holding.location,
        notes=holding.notes,
    ).model_dump()
    changes = item.model_dump(exclude_unset=True)
    if "product_name" in changes and changes["product_name"] is None:
        raise HTTPException(422, "product_name cannot be removed")
    merged = HoldingIn(**{**current, **changes})
    new_matches = update_holding(db, holding, _validated(merged))
    db.commit()
    return HoldingCreated(holding=HoldingOut.of(holding), new_matches=new_matches)


@router.delete("/holdings/{holding_id}", status_code=204, tags=["workspace"])
def delete_holding(holding_id: uuid.UUID, db: DB, key: ApiKeyAuth):
    archive_holding(db, _own_holding(db, key, holding_id))
    db.commit()


@router.get("/matches", response_model=list[MatchOut], tags=["workspace"])
def list_matches(
    db: DB,
    key: ApiKeyAuth,
    status: Literal["open", "resolved", "all"] = "open",
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=200)] = 50,
):
    stmt = select(Match).where(Match.org_id == key.org_id)
    if status != "all":
        stmt = stmt.where(Match.status == status)
    rows = db.scalars(
        stmt.order_by(Match.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .options(selectinload(Match.holding), selectinload(Match.alert))
    ).all()
    lots = first_lots(db, [m.alert_id for m in rows], LIST_BATCHES)
    return [MatchOut.of(m, lots[m.alert_id]) for m in rows]


@router.post("/matches/{match_id}/resolve", response_model=MatchOut, tags=["workspace"])
def resolve(match_id: uuid.UUID, body: ResolveIn, db: DB, key: ApiKeyAuth):
    match = db.scalar(
        select(Match)
        .where(Match.id == match_id, Match.org_id == key.org_id)
        .options(selectinload(Match.holding), selectinload(Match.alert))
    )
    if match is None:
        raise HTTPException(404, "Match not found")
    match.status, match.outcome, match.units_affected, match.note = "resolved", body.outcome, body.units_affected, body.note
    match.resolved_at = datetime.now(UTC)
    audit.record(
        db, "match_resolved", org_id=key.org_id, entity_type="match", entity_id=match.id, via="api", outcome=body.outcome
    )
    db.commit()
    return MatchOut.of(match, first_lots(db, [match.alert_id], LIST_BATCHES)[match.alert_id])


@router.get("/stats", tags=["workspace"])
def workspace_stats(db: DB, key: ApiKeyAuth):
    """Outcome metrics for this workspace."""
    from ...services.metrics import match_metrics

    holdings = db.scalar(
        select(func.count()).select_from(Holding).where(Holding.org_id == key.org_id, Holding.archived_at.is_(None))
    )
    return {"holdings": holdings, **match_metrics(db, key.org_id)}
