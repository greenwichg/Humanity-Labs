import re

from sqlalchemy import select

from batchlight.models import Alert, Match, User
from tests.conftest import post_form, signup


def make_curator(db, email="owner@example.org"):
    user = db.scalar(select(User).where(User.email == email))
    user.is_curator = True
    db.commit()


CURATED = {
    "regulator": "CDSCO (India)",
    "country_code": "IN",
    "title": "Not of standard quality: Coldrif syrup",
    "alert_type": "substandard",
    "severity": "critical",
    "product_names": "COLDRIF",
    "active_ingredients": "paracetamol, phenylephrine, chlorpheniramine",
    "manufacturer": "Sresan Pharmaceutical",
    "batches": "SR-13, 05/2027\nSR-14",
    "reason": "Diethylene glycol contamination",
    "published_at": "2025-10-08",
    "source_url": "https://cdsco.gov.in/opencms/opencms/en/Notifications/Alerts/",
}


def test_console_requires_curator(client, db):
    signup(client)
    assert client.get("/admin").status_code == 403
    assert post_form(client, "/admin/alerts", CURATED).status_code == 403


def test_curated_alert_matches_stock_and_notifies(client, make_client, db):
    signup(client)
    make_curator(db)
    family = make_client()
    signup(family, email="family@example.org", org="Dad's medicines", kind="household", country="IN")
    post_form(family, "/app/inventory", {"product_name": "Coldrif syrup", "lot": "SR 13"})
    assert client.get("/admin").status_code == 200
    invalid = post_form(client, "/admin/alerts", {**CURATED, "source_url": "javascript:alert(1)", "severity": "apocalyptic"})
    assert invalid.status_code == 422
    response = post_form(client, "/admin/alerts", CURATED)
    assert response.status_code == 303
    alert = db.scalar(select(Alert))
    assert alert.regulator == "CDSCO (India)" and alert.country_code == "IN" and alert.source_id == "curated"
    assert [lot.lot_key for lot in alert.lots] == ["SR13", "SR14"]
    match = db.scalar(select(Match))
    assert match and match.level == "batch"
    detail = family.get(f"/app/matches/{match.id}")
    assert "Keep taking the medicine unless told otherwise" in detail.text


def test_curated_csv_import(client, db):
    signup(client)
    make_curator(db)
    template = client.get("/admin/alerts/template.csv").text
    response = client.post(
        "/admin/alerts/import", files={"file": ("a.csv", template, "text/csv")}, data={"csrf_token": client.cookies["bl_csrf"]}
    )
    assert response.status_code == 200 and "Published 1 alert" in response.text
    bad = client.post(
        "/admin/alerts/import",
        files={"file": ("a.csv", "title\nx\n", "text/csv")},
        data={"csrf_token": client.cookies["bl_csrf"]},
    )
    assert bad.status_code == 422 and "Missing column" in bad.text


def test_public_pages_render_with_data(client, ingest):
    ingest("us-fda-drug")
    ingest("uk-mhra")
    for path in [
        "/",
        "/medicines",
        "/check",
        "/check?batch=E941A&product=Tropicamide",
        "/alerts",
        "/alerts?q=heparin&severity=high",
        "/manufacturers",
        "/manufacturers?q=strides",
        "/sources",
        "/impact",
        "/about",
        "/login",
        "/signup",
        "/forgot-password",
        "/api/docs",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
    home = client.get("/")
    assert "Check a medicine batch" in home.text and "Check a food product" in home.text
    assert "Latest serious alerts" in client.get("/medicines").text
    check = client.get("/check?batch=E941A&product=Tropicamide eye drops").text
    assert "Batch E941A is listed" in check
    alert_link = re.search(r'href="(/alerts/[0-9a-f-]{36})"', client.get("/alerts").text).group(1)
    assert "Affected batches" in client.get(alert_link).text
    maker = re.search(r'href="(/manufacturers/[a-z0-9-]+)"', client.get("/manufacturers").text).group(1)
    assert client.get(maker).status_code == 200


def test_security_headers_and_not_found(client):
    response = client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    missing = client.get("/alerts/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404 and "Page not found" in missing.text
    assert client.get("/api/v1/alerts/00000000-0000-0000-0000-000000000000").json() == {"detail": "Alert not found"}
    assert client.get("/healthz").text == "ok"
    assert client.get("/readyz").json() == {"status": "ready"}


def test_untrusted_alert_text_is_escaped(client, db):
    signup(client)
    make_curator(db)
    post_form(
        client,
        "/admin/alerts",
        {**CURATED, "title": "Recall <script>alert(1)</script>", "reason": "<img src=x onerror=alert(1)>"},
    )
    alert = db.scalar(select(Alert))
    page = client.get(f"/alerts/{alert.id}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page
