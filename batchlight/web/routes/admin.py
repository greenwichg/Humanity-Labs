"""Curator console: source health, manual runs and curated alerts."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from ...db import session_factory
from ...ingest import REGISTRY, ensure_sources, run_source, upsert_alert
from ...matching import match_alerts, rescore_open_matches
from ...models import ALERT_TYPES, SEVERITIES, Alert, FoodReport, IngestionRun, Source
from ...services import audit
from ...services.curation import CURATED_CSV_COLUMNS, CuratedAlertInput, parse_curated_csv
from ...services.notifications import notify_new_matches
from ..auth import CSRF, DB, CuratorAuth, flash
from ..countries import COUNTRIES
from ..templating import render

router = APIRouter(prefix="/admin", include_in_schema=False)
log = logging.getLogger("batchlight.admin")


def _run_in_background(source_id: str) -> None:
    try:
        run_source(session_factory(), REGISTRY[source_id])
    except Exception:  # noqa: BLE001 - the failure is also recorded on the ingestion run
        log.exception("manual run of %s failed", source_id)


@router.get("")
def index(request: Request, db: DB, auth: CuratorAuth):
    ensure_sources(db)
    db.commit()
    sources = db.scalars(select(Source).order_by(Source.automated.desc(), Source.name)).all()
    counts = dict(db.execute(select(Alert.source_id, func.count()).group_by(Alert.source_id)).all())
    runs = db.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(25)).all()
    running = {r.source_id for r in runs if r.status == "running"}
    new_reports = db.scalar(select(func.count()).select_from(FoodReport).where(FoodReport.status == "new")) or 0
    return render(
        request,
        "admin/index.html",
        {"sources": sources, "counts": counts, "runs": runs, "running": running, "new_reports": new_reports},
    )


@router.post("/sources/{source_id}/run", dependencies=[CSRF])
def run_now(source_id: str, background: BackgroundTasks, db: DB, auth: CuratorAuth):
    adapter = REGISTRY.get(source_id)
    if adapter is None or not adapter.automated:
        raise HTTPException(404)
    background.add_task(_run_in_background, source_id)
    audit.record(db, "source_run_requested", user_id=auth.user.id, entity_type="source", entity_id=source_id)
    db.commit()
    response = RedirectResponse("/admin", status_code=303)
    flash(response, f"Started a run of {adapter.name}. Refresh to see progress.", "info")
    return response


@router.post("/sources/{source_id}/toggle", dependencies=[CSRF])
def toggle(source_id: str, db: DB, auth: CuratorAuth):
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404)
    source.enabled = not source.enabled
    audit.record(db, "source_toggled", user_id=auth.user.id, entity_type="source", entity_id=source_id, enabled=source.enabled)
    db.commit()
    response = RedirectResponse("/admin", status_code=303)
    flash(response, f"{source.name} is now {'enabled' if source.enabled else 'paused'}.", "info")
    return response


def _publish(db, data: CuratedAlertInput, user_id) -> tuple[Alert, int]:
    ensure_sources(db)
    source = db.get(Source, "curated")
    alert, _, changed = upsert_alert(db, source, data.to_record(), created_by=user_id)
    db.commit()
    if changed:
        rescore_open_matches(db, alert_ids=[alert.id])
    new_matches = match_alerts(db, [alert.id]) if changed else []
    db.commit()
    if new_matches:
        notify_new_matches(db, new_matches)
        db.commit()
    return alert, len(new_matches)


@router.get("/alerts/new")
def new_alert(request: Request, auth: CuratorAuth):
    return render(
        request,
        "admin/alert_form.html",
        {
            "form": {"alert_type": "recall", "severity": "high"},
            "errors": {},
            "types": ALERT_TYPES,
            "severities": SEVERITIES,
            "countries": COUNTRIES,
        },
    )


@router.post("/alerts", dependencies=[CSRF])
async def create_alert(request: Request, db: DB, auth: CuratorAuth):
    form = {k: str(v) for k, v in (await request.form()).items() if k != "csrf_token"}
    try:
        data = CuratedAlertInput(**form)
    except ValidationError as exc:
        errors = {str(e["loc"][0]): e["msg"].removeprefix("Value error, ") for e in exc.errors() if e["loc"]}
        return render(
            request,
            "admin/alert_form.html",
            {"form": form, "errors": errors, "types": ALERT_TYPES, "severities": SEVERITIES, "countries": COUNTRIES},
            status_code=422,
        )
    alert, matched = _publish(db, data, auth.user.id)
    audit.record(db, "curated_alert_created", user_id=auth.user.id, entity_type="alert", entity_id=alert.id, matched=matched)
    db.commit()
    response = RedirectResponse(f"/alerts/{alert.id}", status_code=303)
    flash(response, f"Alert published. It matched {matched} stock item(s) and those workspaces were notified.")
    return response


@router.get("/alerts/import")
def import_form(request: Request, auth: CuratorAuth):
    return render(request, "admin/alert_import.html", {"errors": [], "result": None, "columns": CURATED_CSV_COLUMNS})


@router.get("/alerts/template.csv")
def import_template(auth: CuratorAuth):
    example = (
        "medicine,CDSCO (India),IN,Not of standard quality: Example Paracetamol Syrup,substandard,high,Example Paracetamol Syrup,"
        'paracetamol,Example Labs Pvt Ltd,,"EPS2401, 06/2026; EPS2402, 07/2026",false,Failed assay test,2026-10-15,'
        "https://cdsco.gov.in/opencms/opencms/en/Notifications/Alerts/,\n"
    )
    return PlainTextResponse(
        ",".join(CURATED_CSV_COLUMNS) + "\n" + example,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="batchlight-curated-alerts-template.csv"'},
    )


@router.post("/alerts/import", dependencies=[CSRF])
async def import_alerts(request: Request, db: DB, auth: CuratorAuth, file: Annotated[UploadFile, File()]):
    rows, errors = parse_curated_csv(await file.read(5_000_001))
    if errors:
        return render(
            request,
            "admin/alert_import.html",
            {"errors": errors[:50], "result": None, "columns": CURATED_CSV_COLUMNS},
            status_code=422,
        )
    matched = 0
    for data in rows:
        _, count = _publish(db, data, auth.user.id)
        matched += count
    audit.record(db, "curated_alerts_imported", user_id=auth.user.id, count=len(rows), matched=matched)
    db.commit()
    return render(
        request,
        "admin/alert_import.html",
        {"errors": [], "result": {"alerts": len(rows), "matched": matched}, "columns": CURATED_CSV_COLUMNS},
    )
