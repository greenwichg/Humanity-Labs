"""Consumer Food Safety module: recalls, what testing has found, product checks, reports and testing services."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ...food.knowledge import (
    EVIDENCE_LABELS,
    HOME_TESTING_GENERAL,
    TESTING_FACTS,
    TOPICS,
    TOPICS_BY_SLUG,
    WHY_IT_MATTERS,
    search_topics,
)
from ...food.labs import TEST_LABELS
from ...food.reports import PROBLEM_LABELS, ReportInput, create_report, email_allowed, ip_fingerprint, over_limit
from ...food.verify import FOSCOS_URL, check_barcode, check_fssai_licence
from ...models import Alert, TestingLab
from ...services import audit
from ...services.alerts import first_lots, search_alerts, sources_status
from ...services.email import send_email
from ..auth import CSRF, DB, OptionalAuth, client_ip
from ..countries import COUNTRIES, country_name
from ..templating import render

router = APIRouter(prefix="/food", include_in_schema=False)
COUNTRY_TABS = (("", "All countries"), ("IN", "India"), ("US", "United States"), ("CA", "Canada"), ("HK", "Hong Kong"))
FORM_FIELDS = (
    "problem",
    "product_name",
    "brand",
    "batch",
    "barcode",
    "fssai_licence",
    "purchased_at",
    "city",
    "country_code",
    "description",
    "contact_email",
)
CONTEXT = {"evidence_labels": EVIDENCE_LABELS}


def food_alert_or_404(db, alert_id: uuid.UUID) -> Alert:
    alert = db.scalar(select(Alert).where(Alert.id == alert_id).options(selectinload(Alert.source)))
    if alert is None:
        raise HTTPException(404)
    return alert


@router.get("")
def food_home(request: Request, db: DB, auth: OptionalAuth):
    india = search_alerts(db, category="food", country="IN", per_page=3).items
    latest = db.scalars(
        select(Alert)
        .where(Alert.category == "food")
        .order_by(Alert.published_at.desc().nulls_last())
        .limit(4)
        .options(selectinload(Alert.source))
    ).all()
    return render(
        request,
        "food/home.html",
        {
            **CONTEXT,
            "topics": TOPICS,
            "india": india,
            "latest": latest,
            "why": WHY_IT_MATTERS,
            "sources": sources_status(db, category="food"),
        },
    )


@router.get("/recalls")
def recalls(request: Request, db: DB, auth: OptionalAuth, q: str = "", country: str = "", page: int = Query(1, ge=1, le=1000)):
    q = q.strip()[:200]
    country = country.upper() if country.upper() in {code for code, _ in COUNTRY_TABS} else ""
    result = search_alerts(db, q, category="food", country=country or None, page=page, per_page=20)
    barcode = check_barcode(q) if q.replace(" ", "").isdigit() else None
    return render(
        request,
        "food/recalls.html",
        {
            **CONTEXT,
            "page": result,
            "q": q,
            "country": country,
            "country_label": dict(COUNTRY_TABS).get(country, ""),
            "tabs": COUNTRY_TABS,
            "barcode": barcode,
            "topics": search_topics(q),
            "sources": sources_status(db, category="food"),
        },
    )


@router.get("/recalls/{alert_id}")
def recall_detail(request: Request, alert_id: uuid.UUID, db: DB, auth: OptionalAuth):
    alert = food_alert_or_404(db, alert_id)
    if alert.category != "food":
        return RedirectResponse(f"/alerts/{alert.id}", status_code=302)
    return render(
        request,
        "food/recall_detail.html",
        {
            **CONTEXT,
            "alert": alert,
            "lots": first_lots(db, [alert.id], 300)[alert.id],
            "country": country_name(alert.country_code) if alert.country_code else None,
        },
    )


@router.get("/reported")
def topics(request: Request, auth: OptionalAuth):
    return render(request, "food/topics.html", {**CONTEXT, "topics": TOPICS, "home_testing": HOME_TESTING_GENERAL})


@router.get("/reported/{slug}")
def topic(request: Request, slug: str, db: DB, auth: OptionalAuth):
    item = TOPICS_BY_SLUG.get(slug)
    if item is None:
        raise HTTPException(404)
    # Only curated India-relevant actions: keyword matches against worldwide recalls (e.g. "undeclared milk"
    # allergen recalls for the milk topic) would wrongly suggest adulteration.
    words = [w for w in item.search_terms.split() if len(w) >= 4][:8]
    related = []
    if words:
        related = db.scalars(
            select(Alert)
            .where(Alert.category == "food", Alert.source_id == "curated", or_(*[Alert.title.ilike(f"%{w}%") for w in words]))
            .order_by(Alert.published_at.desc().nulls_last())
            .limit(5)
            .options(selectinload(Alert.source))
        ).all()
    return render(request, "food/topic.html", {**CONTEXT, "topic": item, "related": related})


@router.get("/verify")
def verify(request: Request, db: DB, auth: OptionalAuth, licence: str = "", barcode: str = ""):
    licence_result = check_fssai_licence(licence) if licence.strip() else None
    barcode_result = check_barcode(barcode) if barcode.strip() else None
    barcode_recalls = []
    if barcode_result and barcode_result.valid:
        barcode_recalls = search_alerts(db, barcode_result.search_digits, category="food", per_page=10).items
    return render(
        request,
        "food/verify.html",
        {
            **CONTEXT,
            "licence": licence[:40],
            "barcode": barcode[:40],
            "licence_result": licence_result,
            "barcode_result": barcode_result,
            "barcode_recalls": barcode_recalls,
            "foscos_url": FOSCOS_URL,
        },
    )


def report_context(form: dict | None = None, errors: dict | None = None) -> dict:
    return {
        **CONTEXT,
        "form": form or {"country_code": "IN"},
        "errors": errors or {},
        "problems": PROBLEM_LABELS,
        "countries": COUNTRIES,
    }


@router.get("/report")
def report_form(request: Request, auth: OptionalAuth, product: str = "", batch: str = ""):
    return render(
        request, "food/report.html", report_context({"country_code": "IN", "product_name": product[:300], "batch": batch[:100]})
    )


@router.post("/report", dependencies=[CSRF])
async def submit_report(request: Request, db: DB, auth: OptionalAuth, background: BackgroundTasks):
    form = await request.form()
    values = {name: str(form.get(name, "")) for name in FORM_FIELDS}
    if form.get("website"):  # honeypot field, hidden from people
        return render(
            request,
            "food/report.html",
            report_context(values, {"form": "Something went wrong. Please try again."}),
            status_code=400,
        )
    try:
        data = ReportInput(**values)
    except ValidationError as exc:
        errors = {str(e["loc"][0]) if e["loc"] else "form": e["msg"].removeprefix("Value error, ") for e in exc.errors()}
        return render(request, "food/report.html", report_context(values, errors), status_code=422)
    ip = client_ip(request)
    if over_limit(db, ip_fingerprint(ip)):
        return render(
            request,
            "food/report.html",
            report_context(
                values, {"form": "You have sent several reports in the last hour. Please wait a little before sending another."}
            ),
            status_code=429,
        )
    report = create_report(db, data, ip, auth.user.id if auth else None)
    audit.record(
        db, "food_report_created", user_id=auth.user.id if auth else None, entity_type="food_report", entity_id=report.id
    )
    db.commit()
    if report.contact_email and email_allowed(db, report.contact_email):
        # Only the server-generated reference goes in the email, never text from the form, and it is sent after the
        # response so a slow mail server cannot hold up the request.
        background.add_task(
            send_email,
            report.contact_email,
            f"We received your food safety report {report.reference}",
            f"Thank you for your report to Humanity Labs. Your reference is {report.reference}.\n\n"
            "A curator will review it. We do not publish individual reports. To make an official complaint, use "
            "FSSAI's Food Safety Connect app or contact your state food safety department.\n\n"
            "If anyone is unwell, please seek medical care.\n\n"
            "If you did not send this report, you can ignore this email.",
        )
    return render(request, "food/report_thanks.html", {**CONTEXT, "report": report, "problem": PROBLEM_LABELS[report.problem]})


@router.get("/testing")
def testing(request: Request, db: DB, auth: OptionalAuth, country: str = "IN", test: str = "", city: str = ""):
    country = country.upper()[:2] if country else ""
    stmt = select(TestingLab).where(TestingLab.active.is_(True))
    if country:
        stmt = stmt.where(TestingLab.country_code == country)
    if test in TEST_LABELS:
        stmt = stmt.where(TestingLab.tests.any(test))
    city = city.strip()[:100]
    if city:
        stmt = stmt.where(or_(TestingLab.city.ilike(f"%{city}%"), TestingLab.region.ilike(f"%{city}%")))
    labs = db.scalars(stmt.order_by(TestingLab.region, TestingLab.city, TestingLab.name).limit(200)).all()
    return render(
        request,
        "food/testing.html",
        {
            **CONTEXT,
            "labs": labs,
            "country": country,
            "test": test,
            "city": city,
            "tests": TEST_LABELS,
            "facts": TESTING_FACTS,
            "countries": COUNTRIES,
        },
    )
