import re

from sqlalchemy import func, select

from batchlight.models import Alert, FoodReport, Match, TestingLab, User
from batchlight.services.email import OUTBOX
from tests.conftest import post_form, signup

SURIMI_BARCODE = "6926104942661"


def make_curator(db, email="owner@example.org"):
    user = db.scalar(select(User).where(User.email == email))
    user.is_curator = True
    db.commit()


def surimi(db):
    return db.scalar(select(Alert).where(Alert.title.like("Snow Crab%")))


def test_food_sources_ingest_as_food_with_barcodes_and_codes(ingest, db):
    fda = ingest("us-fda-food")
    assert fda.status == "succeeded" and fda.created == 25
    assert (
        db.scalar(select(func.count()).select_from(Alert).where(Alert.source_id == "us-fda-food", Alert.category != "food")) == 0
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.source_id == "us-fda-food", func.cardinality(Alert.product_codes) > 0)
        )
        >= 12
    )
    cfia = ingest("ca-cfia-food")
    assert cfia.status == "succeeded" and cfia.created == 15
    alert = surimi(db)
    assert alert.category == "food" and alert.severity == "critical" and alert.country_code == "CA"
    assert alert.product_codes == [SURIMI_BARCODE]
    assert [lot.lot for lot in alert.lots] == ["20260802", "20270201", "J016", "D02"]
    assert alert.manufacturer == "Ecological Park Agriculture Inc."
    # Health Canada's medicine adapter reads the same file and must skip CFIA rows
    ingest("ca-health-canada")
    assert (
        db.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.source_id == "ca-health-canada", Alert.raw["Organization"].astext == "CFIA")
        )
        == 0
    )


def test_food_alerts_never_reach_batchlight_stock_or_medicine_pages(client, ingest, db):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Snow Crab Flavoured Surimi Stick", "lot": "J016"})
    ingest("ca-cfia-food")
    client.get("/app")  # show and clear the "added" message
    assert db.scalar(select(func.count()).select_from(Match)) == 0
    alert = surimi(db)
    assert "Snow Crab" not in client.get("/alerts?q=surimi").text
    assert client.get("/api/v1/alerts", params={"q": "surimi"}).json()["total"] == 0
    assert client.get("/api/v1/alerts", params={"q": "surimi", "category": "food"}).json()["total"] == 1
    assert "Snow Crab" not in client.get("/check?batch=J016&product=surimi").text
    moved = client.get(f"/alerts/{alert.id}", follow_redirects=False)
    assert moved.status_code == 301 and moved.headers["location"] == f"/food/recalls/{alert.id}"
    ingest("us-fda-drug")
    drug = db.scalar(select(Alert).where(Alert.category == "medicine"))
    back = client.get(f"/food/recalls/{drug.id}", follow_redirects=False)
    assert back.status_code == 301 and back.headers["location"] == f"/alerts/{drug.id}"


def test_food_recall_search_by_name_batch_barcode_and_country(client, ingest, db):
    ingest("ca-cfia-food")
    from batchlight.food.seed import seed_india_food_actions

    assert seed_india_food_actions(db) == (3, 0)
    db.commit()
    assert seed_india_food_actions(db) == (0, 0)
    for q in ("surimi", "J016", SURIMI_BARCODE, "6 926104 942661"):
        assert "Snow Crab Flavoured Surimi Stick" in client.get("/food/recalls", params={"q": q}).text, q
    india = client.get("/food/recalls", params={"country": "IN"}).text
    assert "Everest Cumin Powder" in india and "Snow Crab" not in india
    assert "E080668761" in client.get("/food/recalls", params={"q": "E080668761"}).text
    miss = client.get("/food/recalls", params={"q": "nonexistent product xyz"}).text
    assert "This is not proof the product is safe or genuine" in miss
    detail = client.get(f"/food/recalls/{surimi(db).id}").text
    assert "If you have this product" in detail and SURIMI_BARCODE in detail and "J016" in detail


def test_verify_tools(client, ingest):
    ingest("ca-cfia-food")
    page = client.get("/food/verify", params={"barcode": SURIMI_BARCODE, "licence": "1001 2345 6789 01"}).text
    assert "correctly formed barcode" in page and "This barcode appears in 1 recall" in page
    assert "The number has the right format" in page and "FoSCoS" in page
    bad = client.get("/food/verify", params={"barcode": "6926104942662", "licence": "123"}).text
    assert "check digit" in bad and "14 digits" in bad
    india = client.get("/food/verify", params={"barcode": "8901058000290"}).text
    assert "GS1 India" in india and "not where the food was made" in india
    assert "Batch, lot or code number" in client.get("/food/verify").text


def test_consumer_pages_render(client):
    for path in [
        "/",
        "/food",
        "/food/recalls",
        "/food/reported",
        "/food/report",
        "/food/testing",
        "/about",
        "/sources",
        "/impact",
    ]:
        assert client.get(path).status_code == 200, path
    for slug in ("overview-india", "milk", "spices", "turmeric", "edible-oils", "honey", "food-colours", "pesticide-residues"):
        page = client.get(f"/food/reported/{slug}")
        assert page.status_code == 200 and "What testing found" in page.text and "Source" in page.text, slug
    assert client.get("/food/reported/unknown").status_code == 404


REPORT = {
    "problem": "looks_wrong",
    "product_name": "Turmeric powder",
    "brand": "Loose, market stall",
    "batch": "",
    "barcode": "",
    "fssai_licence": "",
    "purchased_at": "Weekly market",
    "city": "Patna",
    "country_code": "IN",
    "description": "Very bright colour and it stained everything orange.",
    "contact_email": "",
}


def test_report_validation_and_submission(client, db):
    bad = post_form(client, "/food/report", {**REPORT, "problem": "", "description": "short", "contact_email": "nope"})
    assert bad.status_code == 422
    for message in ("Choose what went wrong", "at least 10 characters", "valid email address"):
        assert message in bad.text
    ok = post_form(client, "/food/report", {**REPORT, "contact_email": "Reporter@Example.org", "barcode": "8901 058 000290"})
    assert ok.status_code == 200
    reference = re.search(r"FR-[A-Z2-9]{8}", ok.text).group(0)
    report = db.scalar(select(FoodReport).where(FoodReport.reference == reference))
    assert report.status == "new" and report.barcode == "8901058000290" and report.contact_email == "reporter@example.org"
    assert report.ip_hash and "testclient" not in report.ip_hash and len(report.ip_hash) == 64
    assert reference in OUTBOX[-1]["Subject"]
    # reports are never shown publicly
    assert reference not in client.get("/food/recalls", params={"q": "turmeric"}).text


def test_report_spam_controls(client, db):
    assert post_form(client, "/food/report", {**REPORT, "website": "http://spam.example"}).status_code == 400
    statuses = [post_form(client, "/food/report", REPORT).status_code for _ in range(6)]
    assert statuses[:5] == [200] * 5 and statuses[5] == 429
    assert db.scalar(select(func.count()).select_from(FoodReport)) == 5
    no_csrf = client.post("/food/report", data=REPORT, follow_redirects=False)
    assert no_csrf.status_code == 400


def test_curator_reviews_reports(client, make_client, db):
    post_form(client, "/food/report", REPORT)
    report = db.scalar(select(FoodReport))
    signup(client)
    assert client.get("/admin/food/reports").status_code == 403
    assert post_form(client, f"/admin/food/reports/{report.id}", {"status": "forwarded"}).status_code == 403
    make_curator(db)
    queue = client.get("/admin/food/reports").text
    assert report.reference in queue
    assert report.reference in client.get(f"/admin/food/reports/{report.id}").text
    done = post_form(client, f"/admin/food/reports/{report.id}", {"status": "forwarded", "curator_note": "Sent to Bihar FSD"})
    assert done.status_code == 303
    db.refresh(report)
    assert (report.status, report.curator_note) == ("forwarded", "Sent to Bihar FSD") and report.reviewed_at
    assert "1 forwarded to an authority" in client.get("/impact").text


LAB = {
    "name": "Example Food Testing Laboratory",
    "city": "Hyderabad",
    "region": "Telangana",
    "country_code": "IN",
    "accreditation": "NABL TC-0000 (example)",
    "source_url": "https://nabl-india.org/",
    "verified_on": "2026-11-01",
    "accepts_public_samples": "yes",
    "website": "",
    "phone": "",
    "notes": "",
    "tests": ["pesticides", "heavy_metals"],
}


def test_lab_directory_curation(client, db):
    signup(client)
    assert client.get("/admin/food/labs").status_code == 403
    make_curator(db)
    invalid = post_form(client, "/admin/food/labs", {**LAB, "source_url": "http://insecure.example", "tests": ["magic"]})
    assert invalid.status_code == 422
    assert db.scalar(select(func.count()).select_from(TestingLab)) == 0
    assert post_form(client, "/admin/food/labs", LAB).status_code == 303
    lab = db.scalar(select(TestingLab))
    assert lab.tests == ["heavy_metals", "pesticides"] and lab.accepts_public_samples is True
    public = client.get("/food/testing", params={"country": "IN", "test": "pesticides", "city": "hyder"}).text
    assert "Example Food Testing Laboratory" in public and "Accepts samples from the public" in public
    assert "Example Food Testing" not in client.get("/food/testing", params={"test": "allergens"}).text
    post_form(client, f"/admin/food/labs/{lab.id}/toggle")
    client.get("/admin/food/labs")  # show and clear the confirmation message
    assert "Example Food Testing" not in client.get("/food/testing").text
    edited = post_form(client, f"/admin/food/labs/{lab.id}", {**LAB, "city": "Secunderabad"})
    assert edited.status_code == 303
    db.refresh(lab)
    assert lab.city == "Secunderabad"


def test_curated_food_alert_goes_to_food_module_only(client, db):
    signup(client)
    make_curator(db)
    post_form(client, "/app/inventory", {"product_name": "Everest Cumin Powder", "lot": "E080668761"})
    form = {
        "category": "food",
        "regulator": "FSSAI (India)",
        "country_code": "IN",
        "title": "Example cumin batch recall",
        "alert_type": "recall",
        "severity": "high",
        "product_names": "Everest Cumin Powder",
        "batches": "E080668761",
        "published_at": "2026-10-04",
        "source_url": "https://www.fssai.gov.in/",
    }
    response = post_form(client, "/admin/alerts", form)
    assert response.status_code == 303
    alert = db.scalar(select(Alert).where(Alert.title == "Example cumin batch recall"))
    assert alert.category == "food" and alert.source_id == "curated"
    assert db.scalar(select(func.count()).select_from(Match)) == 0
    assert "Example cumin batch recall" in client.get("/food/recalls", params={"q": "E080668761"}).text
    assert client.get("/api/v1/alerts", params={"q": "E080668761"}).json()["total"] == 0
