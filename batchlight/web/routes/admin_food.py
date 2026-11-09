"""Curator tools for the Food Safety module: consumer report review and the testing-lab directory."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from ...food.labs import TEST_LABELS, LabInput
from ...food.reports import PROBLEM_LABELS
from ...models import REPORT_STATUSES, FoodReport, TestingLab
from ...services import audit
from ..auth import CSRF, DB, CuratorAuth, flash
from ..countries import COUNTRIES
from ..templating import render

router = APIRouter(prefix="/admin/food", include_in_schema=False)
LAB_FIELDS = ("name", "city", "region", "country_code", "accreditation", "website", "phone", "source_url", "verified_on", "notes")


@router.get("/reports")
def reports(request: Request, db: DB, auth: CuratorAuth, status: str = "new"):
    status = status if status in (*REPORT_STATUSES, "all") else "new"
    stmt = select(FoodReport)
    if status != "all":
        stmt = stmt.where(FoodReport.status == status)
    rows = db.scalars(stmt.order_by(FoodReport.created_at.desc()).limit(200)).all()
    counts = dict(db.execute(select(FoodReport.status, func.count()).group_by(FoodReport.status)).all())
    return render(
        request,
        "admin/food_reports.html",
        {"reports": rows, "status": status, "counts": counts, "problems": PROBLEM_LABELS, "statuses": REPORT_STATUSES},
    )


def _report(db, report_id: uuid.UUID) -> FoodReport:
    report = db.get(FoodReport, report_id)
    if report is None:
        raise HTTPException(404)
    return report


@router.get("/reports/{report_id}")
def report_detail(request: Request, report_id: uuid.UUID, db: DB, auth: CuratorAuth):
    report = _report(db, report_id)
    same_product = db.scalar(
        select(func.count())
        .select_from(FoodReport)
        .where(func.lower(FoodReport.product_name) == report.product_name.lower(), FoodReport.id != report.id)
    )
    return render(
        request,
        "admin/food_report.html",
        {"report": report, "problems": PROBLEM_LABELS, "statuses": REPORT_STATUSES, "same_product": same_product},
    )


@router.post("/reports/{report_id}", dependencies=[CSRF])
async def update_report(request: Request, report_id: uuid.UUID, db: DB, auth: CuratorAuth):
    report = _report(db, report_id)
    form = await request.form()
    status = str(form.get("status", ""))
    if status not in REPORT_STATUSES:
        raise HTTPException(422, "Choose a valid status")
    report.status = status
    report.curator_note = str(form.get("curator_note", "")).strip()[:4000] or None
    report.reviewed_by, report.reviewed_at = auth.user.id, datetime.now(UTC)
    audit.record(db, "food_report_reviewed", user_id=auth.user.id, entity_type="food_report", entity_id=report.id, status=status)
    db.commit()
    response = RedirectResponse("/admin/food/reports", status_code=303)
    flash(response, f"Report {report.reference} marked {status}.")
    return response


def lab_context(form: dict, errors: dict, lab: TestingLab | None = None) -> dict:
    return {"form": form, "errors": errors, "lab": lab, "tests": TEST_LABELS, "countries": COUNTRIES}


@router.get("/labs")
def labs(request: Request, db: DB, auth: CuratorAuth):
    rows = db.scalars(select(TestingLab).order_by(TestingLab.active.desc(), TestingLab.country_code, TestingLab.name)).all()
    return render(request, "admin/labs.html", {"labs": rows, "tests": TEST_LABELS})


@router.get("/labs/new")
def new_lab(request: Request, auth: CuratorAuth):
    return render(
        request, "admin/lab_form.html", lab_context({"country_code": "IN", "verified_on": date.today().isoformat()}, {})
    )


async def _lab_input(request: Request) -> tuple[dict, LabInput | None, dict]:
    form = await request.form()
    values = {name: str(form.get(name, "")) for name in LAB_FIELDS}
    tests = [t for t in form.getlist("tests") if isinstance(t, str)]
    accepts = str(form.get("accepts_public_samples", ""))
    values_for_form = {**values, "tests": tests, "accepts_public_samples": accepts}
    try:
        data = LabInput(**values, tests=tests, accepts_public_samples={"yes": True, "no": False}.get(accepts))
    except ValidationError as exc:
        errors = {str(e["loc"][0]) if e["loc"] else "form": e["msg"].removeprefix("Value error, ") for e in exc.errors()}
        return values_for_form, None, errors
    return values_for_form, data, {}


@router.post("/labs", dependencies=[CSRF])
async def create_lab(request: Request, db: DB, auth: CuratorAuth):
    form, data, errors = await _lab_input(request)
    if data is None:
        return render(request, "admin/lab_form.html", lab_context(form, errors), status_code=422)
    lab = TestingLab(**data.model_dump(), created_by=auth.user.id)
    db.add(lab)
    db.flush()
    audit.record(db, "testing_lab_created", user_id=auth.user.id, entity_type="testing_lab", entity_id=lab.id)
    db.commit()
    response = RedirectResponse("/admin/food/labs", status_code=303)
    flash(response, f"Added {lab.name}.")
    return response


def _lab(db, lab_id: uuid.UUID) -> TestingLab:
    lab = db.get(TestingLab, lab_id)
    if lab is None:
        raise HTTPException(404)
    return lab


@router.get("/labs/{lab_id}/edit")
def edit_lab(request: Request, lab_id: uuid.UUID, db: DB, auth: CuratorAuth):
    lab = _lab(db, lab_id)
    form = {name: "" if getattr(lab, name) is None else str(getattr(lab, name)) for name in LAB_FIELDS}
    form["tests"] = list(lab.tests)
    form["accepts_public_samples"] = {True: "yes", False: "no", None: ""}[lab.accepts_public_samples]
    return render(request, "admin/lab_form.html", lab_context(form, {}, lab))


@router.post("/labs/{lab_id}", dependencies=[CSRF])
async def save_lab(request: Request, lab_id: uuid.UUID, db: DB, auth: CuratorAuth):
    lab = _lab(db, lab_id)
    form, data, errors = await _lab_input(request)
    if data is None:
        return render(request, "admin/lab_form.html", lab_context(form, errors, lab), status_code=422)
    for name, value in data.model_dump().items():
        setattr(lab, name, value)
    lab.updated_at = datetime.now(UTC)
    audit.record(db, "testing_lab_updated", user_id=auth.user.id, entity_type="testing_lab", entity_id=lab.id)
    db.commit()
    response = RedirectResponse("/admin/food/labs", status_code=303)
    flash(response, f"Saved {lab.name}.")
    return response


@router.post("/labs/{lab_id}/toggle", dependencies=[CSRF])
def toggle_lab(lab_id: uuid.UUID, db: DB, auth: CuratorAuth):
    lab = _lab(db, lab_id)
    lab.active = not lab.active
    audit.record(db, "testing_lab_toggled", user_id=auth.user.id, entity_type="testing_lab", entity_id=lab.id, active=lab.active)
    db.commit()
    response = RedirectResponse("/admin/food/labs", status_code=303)
    flash(response, f"{lab.name} is now {'listed' if lab.active else 'hidden'}.", "info")
    return response
