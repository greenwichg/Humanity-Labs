"""Medicine pages for people in India: advice that follows the notice, honest coverage and evidence labels."""

import re

from sqlalchemy import select

from batchlight.models import Alert, Match, Notification
from batchlight.services.curation import CuratedAlertInput, publish_curated
from batchlight.services.email import OUTBOX
from batchlight.services.india_seed import seed_india_alerts
from tests.conftest import post_form, signup

NEWS_URL = "https://news.example.com/test-syrup-batch-nr-1"


def curated(db, **overrides) -> Alert:
    """Publish a hand-added medicine alert like a curator would."""
    data = {
        "regulator": "Drugs Control Administration, Telangana",
        "country_code": "IN",
        "title": "Testsyrup cough syrup batch NR-1: reported as not of standard quality",
        "alert_type": "substandard",
        "severity": "high",
        "product_names": "Testsyrup Cough Syrup",
        "batches": "NR-1",
        "reason": "A news report says a state laboratory found this batch not of standard quality.",
        "published_at": "2025-10-09",
        "source_url": NEWS_URL,
        "evidence": "report",
        **overrides,
    }
    alert, *_ = publish_curated(db, CuratedAlertInput(**data))
    return alert


def alert_id(db, title_part: str):
    return db.scalar(select(Alert.id).where(Alert.title.contains(title_part)))


def results_section(html: str) -> str:
    """The part of /check that shows the outcome, without the form or the source list."""
    start = html.index('aria-live="polite"')
    return html[start : html.index("Sources searched", start)]


def test_advice_follows_the_notice(client, db):
    seed_india_alerts(db)
    page = client.get(f"/alerts/{alert_id(db, 'Coldrif')}").text
    for text in (
        "follow the advice in the original notice",
        "the notice may say do not use it at all",
        "see a doctor now",
        "speak to a pharmacist or doctor before stopping",
        "your state's drug control department",
        "Pharmacovigilance Programme of India on 1800-180-3024",
    ):
        assert text in page, text
    # The advice sits above the batch list, so it shows near the top on phones.
    assert page.index("If your pack matches this alert") < page.index("Affected batches")
    for text in ("don't stop treatment on your own", "Don't stop treatment", "keep taking", "Keep taking"):
        assert text not in page
    check = client.get("/check", params={"batch": "SR-13", "product": "Coldrif"}).text
    assert "follow the advice in the original notice" in check and "see a doctor now" in check
    assert "Patients should keep taking their medicine" not in check and "keep taking" not in check


def test_batch_only_no_match_is_neutral_and_guides_user(client, db):
    page = client.get("/check", params={"batch": "ZZQX9"}).text
    box = results_section(page)
    assert "emerald" not in box
    assert "We found no alert for batch" in box and "ZZQX9" in box
    assert "Search again with the medicine name" in box
    assert "This does not mean the medicine is safe or genuine." in box
    assert "CDSCO" in box and "cdsco.gov.in" in box and "Drug Alert" in box
    assert "1800-180-3024" in box and "your state's drug control department" in box
    assert "Indian alerts are not included yet" not in page
    assert "Not included yet: India's CDSCO monthly drug alerts and state drug controllers' notices." in page
    with_name = results_section(client.get("/check", params={"batch": "ZZQX9", "product": "Zzqxmed"}).text)
    assert "Search again with the medicine name" not in with_name and "1800-180-3024" in with_name


def test_empty_check_form_asks_for_input(client, db):
    assert "Enter a batch number or medicine name." in client.get("/check", params={"batch": "", "product": ""}).text
    assert "Enter a batch number or medicine name." not in client.get("/check").text


def test_indian_alerts_found_by_batch_and_by_search(client, db):
    created, _, _ = seed_india_alerts(db)
    assert created >= 3
    coldrif = client.get("/check", params={"batch": "SR-13"}).text
    assert "Coldrif" in coldrif
    assert "This batch number appears in 1 alert" in coldrif
    assert "Official lab result" in coldrif
    assert "Official test result from Health Ministry of India" in coldrif
    respifresh = client.get("/check", params={"batch": "R01GL2523"}).text
    assert "Respifresh TR" in respifresh and "This batch number appears in 1 alert" in respifresh
    search = client.get("/alerts", params={"q": "cough syrup"}).text
    assert "Coldrif" in search
    assert "What the labels mean" in search
    detail = client.get(f"/alerts/{alert_id(db, 'Coldrif')}").text
    assert "Official lab result" in detail and "Read the official test result" in detail
    assert "Added by our team" in detail and "from an official test result published by Health Ministry of India" in detail
    assert "Official lab result (sansad.in)" in detail  # sidebar source line
    assert "Medicines sold in India usually have different labels" not in detail  # an Indian alert


def test_check_headings_distinguish_batch_all_lots_and_name_matches(client, db, ingest):
    seed_india_alerts(db)
    curated(
        db,
        title="Allbatch syrup: all batches recalled",
        product_names="Allbatch Syrup",
        batches="",
        all_lots=True,
        external_id="test-allbatch",
        source_url="https://www.example.gov/allbatch-notice",
        evidence="official",
    )
    curated(
        db,
        title="Nobatch syrup: batches not listed",
        product_names="Nobatch Syrup",
        batches="",
        external_id="test-nobatch",
    )
    pages = {
        "batch and name": client.get("/check", params={"batch": "SR-13", "product": "Coldrif"}).text,
        "batch only": client.get("/check", params={"batch": "SR-13"}).text,
        "all lots": client.get("/check", params={"batch": "X123", "product": "Allbatch"}).text,
        "name, other batch": client.get("/check", params={"batch": "X123", "product": "Nobatch"}).text,
        "name only": client.get("/check", params={"product": "Coldrif"}).text,
    }
    assert "Your batch is listed in 1 alert. Check the details below." in pages["batch and name"]
    assert "Batch and name match" in pages["batch and name"]
    assert "but you did not enter a medicine name" in pages["batch only"]
    assert "1 alert covers all batches of a product with this name." in pages["all lots"]
    assert "Name matches; alert covers all batches" in pages["all lots"]
    assert "We found 1 alert about products with a similar name. None lists your batch." in pages["name, other batch"]
    assert "Name matches; batch not confirmed" in pages["name, other batch"]
    assert "We found 1 alert about products with a similar name. Check whether" in pages["name only"]
    # Name-only matches are not shown as an alarm; listed batches and all-batch alerts are.
    assert "bg-red-50" not in results_section(pages["name, other batch"])
    assert "bg-red-50" not in results_section(pages["name only"])
    assert "bg-red-50" in results_section(pages["batch only"]) and "bg-red-50" in results_section(pages["all lots"])
    for page in pages.values():
        assert "Confidence" not in page
        assert "may concern this" not in page

    ingest("us-fda-drug")
    foreign = client.get("/check", params={"batch": "7717825A", "product": "Cinacalcet 30 mg"}).text
    assert "Medicines sold in India usually have different labels and batch numbers" in foreign
    assert "Open the FDA's recall record (data file)" in foreign
    fda_detail = client.get(f"/alerts/{alert_id(db, 'Cinacalcet')}").text
    assert "Medicines sold in India usually have different labels and batch numbers" in fda_detail
    assert "Open the FDA's recall record (data file)" in fda_detail
    assert "Medicines sold in India usually have different labels" not in pages["batch only"]


def test_medicine_pages_state_coverage_and_label_stats(client, db):
    medicines = client.get("/medicines").text
    assert "batch and serial numbers you can check (most are US medical device serial numbers)" in medicines
    assert (
        "(medicines and medical devices)" in medicines
        and "of alerts identify specific batches (mostly US device recalls)" in medicines
    )
    assert "Check this batch" in medicines and "(strongly recommended)" in medicines
    assert "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/188/AU2186_7bn9iE.pdf?source=lsapps" in medicines
    assert "3,012 were declared Not of Standard Quality and 283 spurious or adulterated" in medicines
    assert "CDSCO" in medicines
    for text in ("regulators</p>", "one medical product in ten", "any regulator flags", "Check against"):
        assert text not in medicines
    assert "(strongly recommended)" in client.get("/check").text
    empty = client.get("/alerts", params={"q": "zzzzqx"}).text
    assert "No alerts match these filters." in empty and "CDSCO" in empty and "not included yet" in empty
    assert "Medicine and medical device alerts" in empty
    assert "Indian alerts are not included yet" not in empty
    manufacturers = client.get("/manufacturers").text
    assert "CDSCO" in manufacturers and "across all regulators" not in manufacturers


def test_curated_medicine_news_report_is_never_called_official(client, db):
    signup(client, org="Dad's medicines", kind="household", country="IN")
    post_form(client, "/app/inventory", {"product_name": "Testsyrup syrup", "lot": "NR 1"})
    news = curated(db)
    assert news.evidence == "report" and news.classification is None

    detail = client.get(f"/alerts/{news.id}").text
    assert "News or NGO report" in detail and "Read the news report" in detail
    assert "action, as reported by news.example.com" in detail
    assert "from a news report (news.example.com)" in detail and "Check with your pharmacist" in detail
    assert "News or NGO report (news.example.com)" in detail  # sidebar source line
    assert "What was reported" in detail

    check = client.get("/check", params={"batch": "NR-1", "product": "Testsyrup"}).text
    assert "News or NGO report" in check and "News report (news.example.com)" in check

    match = db.scalar(select(Match).where(Match.alert_id == news.id))
    assert match is not None and match.level == "batch"
    workspace = client.get(f"/app/matches/{match.id}").text
    assert "News or NGO report" in workspace and "Read the news report" in workspace
    assert "If your pack matches this alert" in workspace and "see a doctor now" in workspace
    assert "Keep taking the medicine" not in workspace

    for page in (detail, check, workspace):
        for text in (
            "Read the official notice",
            "Read the original notice",
            "Original notice from",
            "Open the regulator's notice",
        ):
            assert text not in page
        assert '<span class="tag">Curated</span>' not in page

    notice = db.scalar(select(Notification).where(Notification.match_id == match.id))
    assert notice.body.startswith("[News report] Testsyrup syrup batch NR 1: ")
    assert "[News report]" in client.get("/app/notifications").text
    assert OUTBOX and "[News report]" in OUTBOX[-1].get_content()
    assert "keep taking their medicine" not in OUTBOX[-1].get_content()


def test_short_word_does_not_match_unrelated_product(client, db):
    seed_india_alerts(db)
    curated(
        db,
        title="Polyacrylonitrile (PAN) based filter membranes: possible leak",
        product_names="Polyacrylonitrile (PAN) based filter membrane",
        alert_type="recall",
        severity="moderate",
        batches="",
        external_id="test-pan-membrane",
    )
    pan = client.get("/check", params={"product": "Pan 40"}).text
    assert "We found no alert for Pan 40" in pan
    assert "filter membrane" not in results_section(pan)
    membrane = client.get("/check", params={"product": "PAN filter membrane"}).text
    assert "filter membranes: possible leak" in membrane
    assert "Coldrif" in client.get("/check", params={"product": "Coldrif"}).text
    assert re.search(
        r"Your batch is listed in 1 alert", client.get("/check", params={"batch": "SR-13", "product": "Coldrif"}).text
    )
