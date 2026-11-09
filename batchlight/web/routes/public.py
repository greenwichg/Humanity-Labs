"""Public pages: consumer home, medicine module (batch check, alerts, manufacturers), sources, impact, health checks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from ...food.knowledge import TOPICS
from ...models import ALERT_TYPES, SEVERITIES, Alert, Match, Source
from ...services import metrics
from ...services.alerts import check_batch, first_lots, manufacturer_alerts, manufacturer_index, search_alerts, sources_status
from ..auth import DB, OptionalAuth
from ..templating import render

router = APIRouter()
LOTS_SHOWN = 300


def recent_alerts(db, category: str, limit: int) -> list[Alert]:
    return db.scalars(
        select(Alert)
        .where(Alert.category == category, Alert.severity.in_(("critical", "high")))
        .order_by(Alert.published_at.desc().nulls_last(), Alert.created_at.desc())
        .limit(limit)
        .options(selectinload(Alert.source))
    ).all()


@router.get("/", include_in_schema=False)
def home(request: Request, db: DB, auth: OptionalAuth):
    """Consumer-first landing page for medicines and food."""
    counts = dict(db.execute(select(Alert.category, func.count()).group_by(Alert.category)).all())
    return render(
        request,
        "public/consumer_home.html",
        {
            "medicine_alerts": recent_alerts(db, "medicine", 3),
            "food_alerts": recent_alerts(db, "food", 3),
            "counts": counts,
            "topics": TOPICS[1:5],
        },
    )


@router.get("/medicines", include_in_schema=False)
def medicines(request: Request, db: DB, auth: OptionalAuth):
    """Batchlight: medicine safety alerts, for the public and for pharmacies and clinics."""
    return render(
        request,
        "public/medicines.html",
        {"recent": recent_alerts(db, "medicine", 6), "alert_stats": metrics.alert_metrics(db), "sources": sources_status(db)},
    )


@router.get("/check", include_in_schema=False)
def check(request: Request, db: DB, auth: OptionalAuth, batch: str = "", product: str = "", manufacturer: str = ""):
    batch, product, manufacturer = batch.strip()[:100], product.strip()[:200], manufacturer.strip()[:200]
    searched = bool(batch or product)
    results = check_batch(db, batch, product or None, manufacturer or None) if searched else []
    return render(
        request,
        "public/check.html",
        {
            "batch": batch,
            "product": product,
            "manufacturer": manufacturer,
            "searched": searched,
            "results": results,
            "sources": sources_status(db),
        },
    )


@router.get("/alerts", include_in_schema=False)
def alerts(
    request: Request,
    db: DB,
    auth: OptionalAuth,
    q: str = "",
    source: str = "",
    severity: str = "",
    type: str = "",
    page: int = Query(1, ge=1, le=10_000),
):
    severity = severity if severity in SEVERITIES else ""
    type = type if type in ALERT_TYPES else ""
    result = search_alerts(db, q, source or None, severity or None, type or None, page)
    return render(
        request,
        "public/alerts.html",
        {
            "page": result,
            "q": q,
            "source": source,
            "severity": severity,
            "type": type,
            "sources": db.scalars(select(Source).where(Source.category == "medicine").order_by(Source.name)).all(),
        },
    )


@router.get("/alerts/{alert_id}", include_in_schema=False)
def alert_detail(request: Request, alert_id: uuid.UUID, db: DB, auth: OptionalAuth):
    alert = db.scalar(select(Alert).where(Alert.id == alert_id).options(selectinload(Alert.source)))
    if alert is None:
        raise HTTPException(404)
    if alert.category == "food":
        return RedirectResponse(f"/food/recalls/{alert.id}", status_code=302)
    lots = first_lots(db, [alert.id], LOTS_SHOWN)[alert.id]
    own_matches = []
    if auth and auth.org:
        own_matches = db.scalars(
            select(Match).where(Match.alert_id == alert.id, Match.org_id == auth.org.id).options(selectinload(Match.holding))
        ).all()
    return render(request, "public/alert_detail.html", {"alert": alert, "lots": lots, "own_matches": own_matches})


@router.get("/manufacturers", include_in_schema=False)
def manufacturers(request: Request, db: DB, auth: OptionalAuth, q: str = ""):
    return render(request, "public/manufacturers.html", {"rows": manufacturer_index(db, q.strip()[:100]), "q": q})


@router.get("/manufacturers/{slug}", include_in_schema=False)
def manufacturer(request: Request, slug: str, db: DB, auth: OptionalAuth):
    name, items = manufacturer_alerts(db, slug)
    if not items:
        raise HTTPException(404)
    regulators = sorted({a.regulator for a in items})
    return render(request, "public/manufacturer.html", {"name": name, "alerts": items, "regulators": regulators})


@router.get("/sources", include_in_schema=False)
def sources(request: Request, db: DB, auth: OptionalAuth):
    return render(
        request,
        "public/sources.html",
        {"sections": [("Medicines", sources_status(db, "medicine")), ("Food", sources_status(db, "food"))]},
    )


@router.get("/impact", include_in_schema=False)
def impact(request: Request, db: DB, auth: OptionalAuth):
    return render(
        request,
        "public/impact.html",
        {
            "alerts": metrics.alert_metrics(db),
            "matches": metrics.match_metrics(db),
            "reach": metrics.reach_metrics(db),
            "food": metrics.food_metrics(db),
        },
    )


@router.get("/about", include_in_schema=False)
def about(request: Request, auth: OptionalAuth):
    return render(request, "public/about.html")


@router.get("/healthz", include_in_schema=False)
def healthz():
    return PlainTextResponse("ok")


@router.get("/readyz", include_in_schema=False)
def readyz(db: DB):
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "database unavailable"}, status_code=503)
    return {"status": "ready"}


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /app\nDisallow: /admin\nDisallow: /invite\nDisallow: /reset-password\n")
