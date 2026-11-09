import csv
import io
import re
from pathlib import Path

from sqlalchemy import select, text

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
    "source_url": "https://cdsco.gov.in/",
    # A CDSCO "Not of Standard Quality" entry is an official lab result on a government site.
    "evidence": "lab_test",
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
    assert alert.evidence == "lab_test" and alert.classification is None
    assert [lot.lot_key for lot in alert.lots] == ["SR13", "SR14"]
    match = db.scalar(select(Match))
    assert match and match.level == "batch"
    detail = family.get(f"/app/matches/{match.id}")
    assert "Follow the advice in the original notice" in detail.text and "Official lab result" in detail.text
    assert "Keep taking the medicine" not in detail.text


def test_curated_csv_import(client, db):
    signup(client)
    make_curator(db)
    template = client.get("/admin/alerts/template.csv").text
    header, example = list(csv.reader(io.StringIO(template)))
    assert header[-1] == "evidence" and example[-1] == "lab_test"
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
    without_evidence = "\n".join(",".join(row[:-1]) for row in csv.reader(io.StringIO(template))) + "\n"
    rejected = client.post(
        "/admin/alerts/import",
        files={"file": ("a.csv", without_evidence, "text/csv")},
        data={"csrf_token": client.cookies["bl_csrf"]},
    )
    assert rejected.status_code == 422 and "Missing column(s): evidence" in rejected.text
    assert len(db.scalars(select(Alert)).all()) == 1


def test_admin_rejects_official_label_for_news_link(client, db):
    signup(client)
    make_curator(db)
    news = {**CURATED, "evidence": "official", "source_url": "https://www.businesstoday.in/india/story/example-recall"}
    response = post_form(client, "/admin/alerts", news)
    assert response.status_code == 422
    error = re.search(r'<p id="e-evidence" class="field-error">([^<]*)</p>', response.text)
    assert error and "government, Parliament or WHO website" in error.group(1)
    assert 'aria-invalid="true"' in response.text
    missing = post_form(client, "/admin/alerts", {k: v for k, v in CURATED.items() if k != "evidence"})
    assert missing.status_code == 422 and 'id="e-evidence"' in missing.text
    assert db.scalar(select(Alert)) is None
    accepted = post_form(client, "/admin/alerts", {**news, "evidence": "report"})
    assert accepted.status_code == 303
    assert db.scalar(select(Alert)).evidence == "report"


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


COVERAGE_PAGES = ["/", "/medicines", "/check", "/sources", "/about"]
FORBIDDEN_COVERAGE_CLAIMS = [
    "any regulator flags",
    "add notices from India's FSSAI by hand",
    "find a lab you can trust",
    "who to trust",
    "Is it safe?",
    "regulators worldwide",
]


def test_coverage_disclosed(client):
    for path in COVERAGE_PAGES:
        page = client.get(path).text.replace("&#39;", "'")
        assert "CDSCO" in page and "not included" in page.lower(), path
        for claim in FORBIDDEN_COVERAGE_CLAIMS:
            assert claim not in page, (path, claim)
    home = client.get("/").text
    assert "(strongly recommended)" in home and "B.No." in home
    assert "Recent serious medicine and medical device alerts" in home and "Recent serious food recalls (worldwide" in home


def test_advice_follows_the_notice_on_home_and_about(client):
    for path in ("/", "/about"):
        page = client.get(path).text.replace("&#39;", "'")
        assert "If an alert's notice says not to use a product, follow it" in page, path
        assert "Don't stop a medicine without" not in page and "Don't stop taking a medicine without" not in page, path


def test_header_footer_and_error_page(client):
    page = client.get("/").text
    header = page[page.index("<header") : page.index("</header>")]
    footer = page[page.index("<footer") : page.index("</footer>")]
    assert "Report a food problem" in header and "Report food" in header and "Report a problem" not in header
    assert "some news reports" in footer and "official safety alerts" not in page
    assert "data-menu" in header
    missing = client.get("/alerts/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert "Check a medicine batch" in missing.text and 'href="/food"' in missing.text


def test_sources_page(client, db):
    page = client.get("/sources").text.replace("&#39;", "'")
    assert "Not covered yet" in page and "cdsco.gov.in" in page
    cards = page.split('<div class="card card-pad">')
    curated = [card for card in cards if "Humanity Labs team" in card]  # listed under Medicines and under Food
    assert curated
    for card in curated:
        assert "How we add alerts by hand" in card and "Regulator's page" not in card
        assert '<span class="tag">Added by our team</span>' in card
    assert '<span class="tag">Curated</span>' not in page and "Curated alerts link" not in page
    automated = [card for card in cards if ">Automated</span>" in card]
    assert automated and all("Regulator's page" in card for card in automated)


def test_list_rows_show_evidence_and_news_attribution(client, db):
    from batchlight.services.india_seed import seed_india_alerts

    seed_india_alerts(db)
    home = client.get("/").text
    assert "Official lab result" in home
    food = client.get("/food/recalls", params={"country": "IN"}).text
    assert "FSSAI (India) action, as reported by businesstoday.in" in food
    assert "Added by curators" not in food
    medicines = client.get("/alerts", params={"q": "cough syrup"}).text
    assert "Official lab result" in medicines and "as reported by" not in medicines


def test_migration_backfills_curated_evidence(db):
    from alembic import command
    from alembic.config import Config

    from batchlight.db import get_engine

    db.close()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(config, "0005_food_safety")
    try:
        engine = get_engine()
        engine.dispose()  # drop pooled connections that saw the newer schema
        insert = text(
            "INSERT INTO alerts (source_id, external_id, regulator, alert_type, severity, title, source_url, content_hash, "
            "classification) VALUES (:source, :external, 'Test regulator', 'recall', 'high', 'Test alert', :url, 'x', :classification)"
        )
        with engine.begin() as conn:
            for source, external, url, classification in [
                ("curated", "news", "https://www.businesstoday.in/india/story/example", "Curated"),
                ("curated", "official", "https://www.fssai.gov.in/docs/example.pdf", "Curated"),
                ("us-fda-drug", "fda", "https://api.fda.gov/drug/enforcement.json", "Class I"),
            ]:
                conn.execute(insert, {"source": source, "external": external, "url": url, "classification": classification})
        command.upgrade(config, "head")
        engine.dispose()
        with engine.connect() as conn:
            rows = {
                r.external_id: (r.evidence, r.classification)
                for r in conn.execute(text("SELECT external_id, evidence, classification FROM alerts"))
            }
        assert rows["news"] == ("report", None)
        assert rows["official"] == ("official", None)
        assert rows["fda"] == ("official", "Class I")
    finally:
        command.upgrade(config, "head")
        get_engine().dispose()
