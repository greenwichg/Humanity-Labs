import re
from datetime import date

from sqlalchemy import func, select

from batchlight.models import Alert, FoodReport, Match, TestingLab, User
from batchlight.services.email import OUTBOX
from batchlight.services.india_seed import seed_india_alerts
from batchlight.web.countries import country_name
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
    assert moved.status_code == 302 and moved.headers["location"] == f"/food/recalls/{alert.id}"
    ingest("us-fda-drug")
    drug = db.scalar(select(Alert).where(Alert.category == "medicine"))
    back = client.get(f"/food/recalls/{drug.id}", follow_redirects=False)
    assert back.status_code == 302 and back.headers["location"] == f"/alerts/{drug.id}"


def test_food_recall_search_by_name_batch_barcode_and_country(client, ingest, db):
    ingest("ca-cfia-food")
    assert seed_india_alerts(db) == (8, 0, 0)
    assert seed_india_alerts(db) == (0, 0, 0)
    assert db.scalar(select(func.count()).select_from(Alert).where(Alert.source_id == "curated", Alert.category == "food")) == 5
    for q in ("surimi", "J016", SURIMI_BARCODE, "6 926104 942661"):
        assert "Snow Crab Flavoured Surimi Stick" in client.get("/food/recalls", params={"q": q}).text, q
    india = client.get("/food/recalls", params={"country": "IN"}).text
    assert "Everest Cumin Powder" in india and "Snow Crab" not in india
    assert "E080668761" in client.get("/food/recalls", params={"q": "E080668761"}).text
    miss = client.get("/food/recalls", params={"q": "nonexistent product xyz"}).text
    assert "This is not proof the product is safe or genuine" in miss and "we list very few from India" in miss
    detail = client.get(f"/food/recalls/{surimi(db).id}").text
    assert "If you have this product" in detail and SURIMI_BARCODE in detail and "J016" in detail


def test_verify_tools(client, ingest):
    ingest("ca-cfia-food")
    page = client.get("/food/verify", params={"barcode": SURIMI_BARCODE, "licence": "1001 2345 6789 01"}).text
    assert "correctly formed barcode" in page and "This barcode appears in 1 recall" in page
    assert "The number has the right format" in page and "That does not show it is real" in page and "FoSCoS" in page
    assert "food business search" in page
    bad = client.get("/food/verify", params={"barcode": "6926104942662", "licence": "123"}).text
    assert "check digit" in bad and "14 digits" in bad and "Check you copied all 14 digits" in bad
    india = client.get("/food/verify", params={"barcode": "8901058000290"}).text
    assert "GS1 India" in india and "not where the food was made" in india
    assert "Batch, lot or code number" in client.get("/food/verify").text
    short = client.get("/food/verify", params={"barcode": "04963406"}).text
    assert "UPC-E" in short and "049000006346" in short


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
    email = OUTBOX[-1]
    assert reference in email["Subject"] and reference in email.get_content()
    # nothing typed into the form is echoed to the address given, so the form can't be used to send someone spam
    for typed in ("stained everything", "Turmeric", "Patna", "Weekly market"):
        assert typed not in email.get_content() and typed not in email["Subject"]
    # reports are never shown publicly
    assert reference not in client.get("/food/recalls", params={"q": "turmeric"}).text


def test_acknowledgement_emails_are_capped_per_address(client, db):
    before = len(OUTBOX)
    for _ in range(4):
        assert post_form(client, "/food/report", {**REPORT, "contact_email": "target@example.org"}).status_code == 200
    sent = [m for m in OUTBOX[before:] if m["To"] == "target@example.org"]
    assert len(sent) == 3
    assert db.scalar(select(func.count()).select_from(FoodReport)) == 4


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
    allergens = client.get("/food/testing", params={"test": "allergens"}).text
    assert "Example Food Testing" not in allergens and "No listed lab matches these filters" in allergens
    assert "We have not listed any labs yet" not in allergens
    post_form(client, f"/admin/food/labs/{lab.id}/toggle")
    client.get("/admin/food/labs")  # show and clear the confirmation message
    hidden = client.get("/food/testing").text
    assert "Example Food Testing" not in hidden and "We have not listed any labs yet" in hidden
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
        "evidence": "official",
    }
    response = post_form(client, "/admin/alerts", form)
    assert response.status_code == 303
    alert = db.scalar(select(Alert).where(Alert.title == "Example cumin batch recall"))
    assert alert.category == "food" and alert.source_id == "curated"
    assert db.scalar(select(func.count()).select_from(Match)) == 0
    assert "Example cumin batch recall" in client.get("/food/recalls", params={"q": "E080668761"}).text
    assert client.get("/api/v1/alerts", params={"q": "E080668761"}).json()["total"] == 0
    page = client.get(f"/food/recalls/{alert.id}").text
    assert "Added by our team" in page and "FSSAI (India)'s own notice" in page and "Read the official notice" in page
    assert "Official source (fssai.gov.in)" in page and "as the notice says" in page


def test_moving_a_curated_alert_to_food_removes_its_stock_matches(client, db):
    from batchlight.services.curation import CuratedAlertInput, publish_curated

    signup(client)
    make_curator(db)
    post_form(client, "/app/inventory", {"product_name": "Example Paracetamol Syrup", "lot": "EPS2401"})
    user = db.scalar(select(User))
    fields = {
        "regulator": "CDSCO (India)",
        "country_code": "IN",
        "title": "Not of standard quality: Example Paracetamol Syrup",
        "alert_type": "substandard",
        "severity": "high",
        "product_names": "Example Paracetamol Syrup",
        "batches": "EPS2401",
        "published_at": "2026-10-15",
        "source_url": "https://cdsco.gov.in/opencms/opencms/en/Notifications/Alerts/",
        "external_id": "curated-example-1",
        "evidence": "official",
    }
    _, created, _, matched = publish_curated(db, CuratedAlertInput(category="medicine", **fields), user.id)
    assert created and matched == 1 and db.scalar(select(func.count()).select_from(Match)) == 1
    alert, created, changed, matched = publish_curated(db, CuratedAlertInput(category="food", **fields), user.id)
    assert alert.category == "food" and not created and changed and matched == 0
    assert db.scalar(select(func.count()).select_from(Match)) == 0


def test_api_and_sources_page_report_the_right_sources(client, ingest):
    ingest("ca-cfia-food")
    ingest("us-fda-drug")
    food = client.get("/api/v1/check", params={"batch": "J016", "product": "surimi", "category": "food"}).json()
    searched = {s["id"] for s in food["sources_searched"]}
    assert "ca-cfia-food" in searched and "us-fda-drug" not in searched
    assert food["results"] and food["results"][0]["batch_listed"] is True
    medicine = client.get("/api/v1/check", params={"batch": "J016", "product": "surimi"}).json()
    assert "ca-cfia-food" not in {s["id"] for s in medicine["sources_searched"]} and medicine["results"] == []
    assert {s["id"] for s in client.get("/api/v1/sources", params={"category": "food"}).json()} >= {"ca-cfia-food", "us-fda-food"}
    assert "us-fda-food" not in {s["id"] for s in client.get("/api/v1/sources").json()}
    page = client.get("/sources").text
    assert "Medicines" in page and "Canadian Food Inspection Agency food recalls" in page
    assert page.index("Medicines") < page.index("Canadian Food Inspection Agency food recalls")


def test_curated_recall_pages_say_where_they_came_from(client, db):
    seed_india_alerts(db)
    everest = db.scalar(select(Alert).where(Alert.title.like("%Everest Cumin%")))
    assert everest.evidence == "report"
    page = client.get(f"/food/recalls/{everest.id}").text
    assert "News or NGO report" in page and "Added by our team" in page and "Read the news report" in page
    assert "action, as reported by businesstoday.in" in page and "We could not link to FSSAI (India)'s own notice" in page
    assert "News or NGO report (businesstoday.in)" in page and "What was reported" in page
    assert "If they match, don't eat" in page and "Keep it aside and ask the shop or the company" in page
    for wrong in ("as the notice says", "Read the original notice", "Curated", "entered from regulator notices", "curators"):
        assert wrong not in page, wrong
    patanjali = db.scalar(select(Alert).where(Alert.title.like("Patanjali red chilli%")))
    assert patanjali.lot_count == 0 and not patanjali.product_codes and not patanjali.all_lots
    page = client.get(f"/food/recalls/{patanjali.id}").text
    assert "We don't list batch numbers or barcodes for this" in page and "No batch numbers are listed in this source" in page
    assert "(India) (India)" not in page and "If they match, don't eat" not in page and "can't tell which packs" not in page
    listing = client.get("/food/recalls", params={"country": "IN"}).text
    assert "What the labels mean" in listing and "Official lab result" in listing
    # Each list row carries its own label (macros.food_alert_row).
    assert "Added by curators" not in listing and "as reported by businesstoday.in" in listing


def test_official_lab_result_from_hong_kong_is_labelled_and_names_the_country_once(client, db):
    assert country_name("HK") == "Hong Kong"
    seed_india_alerts(db)
    hong_kong = db.scalar(select(Alert).where(Alert.country_code == "HK"))
    assert hong_kong.evidence == "lab_test"
    page = client.get(f"/food/recalls/{hong_kong.id}").text
    assert "Official lab result" in page and "Read the official test result" in page
    assert "from an official test result published by Centre for Food Safety (Hong Kong)" in page
    assert "Official lab result (cfs.gov.hk)" in page and "Why it was recalled" in page
    assert "Read the source to see which packs are affected" in page
    for wrong in ("can't tell which packs", "(HK)", "(Hong Kong) (Hong Kong)", "Read the original notice", "as reported by"):
        assert wrong not in page, wrong
    topic = client.get("/food/reported/spices").text
    assert "What the labels mean" in topic and "Peer-reviewed study" in topic and "Provider&#39;s own information" in topic


def test_no_green_reassurance(client):
    checks = [
        ("/food/recalls", {"q": "zzzz"}),
        ("/food/verify", {"licence": "00000000000000"}),
        ("/food/verify", {"barcode": "4006381333931"}),
    ]
    for path, params in checks:
        page = client.get(path, params=params)
        assert page.status_code == 200 and "emerald" not in page.text, (path, params)
    licence = client.get("/food/verify", params={"licence": "00000000000000"}).text
    assert "That does not show it is real" in licence
    assert "correctly formed barcode" in client.get("/food/verify", params={"barcode": "4006381333931"}).text


def test_testing_page_honest(client, db):
    page = client.get("/food/testing").text
    assert "labs whose accreditation a curator has checked" not in page and "curators" not in page
    for text in (
        "nabl-india.org/lab-accreditation/",
        "tell the seller",
        "fee is refunded",
        "A report from a private lab is not a Section 40 report",
        "We have not listed any labs yet",
        ">FSSAI's website</a>",
        "Labs checked by our team",
        'id="labs"',
        'action="#labs"',
    ):
        assert text in page, text
    assert "14 days" not in page and "No listed lab matches" not in page
    assert "We have not listed any labs yet" in client.get("/food/testing", params={"test": "pesticides"}).text
    db.add(
        TestingLab(
            name="Staged Example Food Laboratory",
            city="Kharar",
            region="Punjab",
            country_code="IN",
            accreditation="NABL TC-0000 (example)",
            tests=["pesticides"],
            source_url="https://nabl-india.org/",
            verified_on=date(2026, 11, 9),
            active=False,
        )
    )
    db.commit()
    for params in ({}, {"city": "Kharar"}, {"country": "", "test": "pesticides"}):
        page = client.get("/food/testing", params=params).text
        assert "Staged Example Food Laboratory" not in page and "We have not listed any labs yet" in page, params


def test_report_routes_and_messages(client):
    page = client.get("/food/report").text
    for text in (
        "foscos.fssai.gov.in/consumergrievance/",
        "1915",
        "88000 01915",
        "1800114000",
        "1800-11-2100",
        "helpdesk-foscos@fssai.gov.in",
        "Section 40",
        'href="/food/testing"',
    ):
        assert text in page, text
    assert "test samples and take action" not in page
    short = post_form(client, "/food/report", {**REPORT, "product_name": "x"})
    assert short.status_code == 422
    assert "Type the product name as it is printed on the pack" in short.text
    assert "String should have at least" not in short.text
    blank = post_form(client, "/food/report", {**REPORT, "product_name": "", "description": ""})
    assert "Type the product name as it is printed on the pack" in blank.text
    assert "Please tell us a little more about what happened (at least 10 characters)" in blank.text
    assert "Input should be" not in blank.text and "String should" not in blank.text
    too_long = post_form(client, "/food/report", {**REPORT, "brand": "b" * 250})
    assert "Please shorten this to 200 characters or fewer" in too_long.text and "String should" not in too_long.text
    thanks = post_form(client, "/food/report", REPORT).text
    assert "may be passed to the relevant" not in thanks and "we can't promise it will act" in thanks
    assert "foscos.fssai.gov.in/consumergrievance/" in thanks and "1800-11-2100" in thanks


def test_report_form_errors_are_accessible(client):
    bad = post_form(client, "/food/report", {**REPORT, "problem": "", "description": "short"})
    assert bad.status_code == 422
    assert '<p id="e-problem" class="field-error">' in bad.text and '<fieldset aria-describedby="e-problem">' in bad.text
    summary = bad.text[bad.text.index('id="error-summary"') :]
    summary = summary[: summary.index("</div>")]
    assert 'tabindex="-1"' in summary and "autofocus" in summary
    assert 'href="#f-problem"' in summary and 'href="#f-description"' in summary and 'href="#f-product_name"' not in summary
    assert 'id="f-problem"' in bad.text and 'id="f-description"' in bad.text
    assert 'aria-describedby="e-problem"' not in client.get("/food/report").text
