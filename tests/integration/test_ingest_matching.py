from sqlalchemy import func, select

from batchlight.models import Alert, AlertLot, IngestionRun, Match, Notification, Source
from batchlight.services.email import OUTBOX
from tests.conftest import post_form, signup


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_fda_ingest_is_idempotent_and_extracts_batches(ingest, db):
    run = ingest("us-fda-drug")
    assert run.status == "succeeded" and run.fetched == 25 and run.created == 25  # includes one record with no recall number
    cinacalcet = db.scalar(select(Alert).where(Alert.external_id == "D-0079-2027"))
    assert cinacalcet.severity == "high" and cinacalcet.alert_type == "recall"
    assert [lot.lot_key for lot in cinacalcet.lots] == ["7717825A"]
    assert "cinacalcet" in cinacalcet.name_tokens
    assert "64380-883" in cinacalcet.product_codes
    assert cinacalcet.manufacturer_key == "strides pharma"
    argatroban = db.scalar(select(Alert).where(Alert.external_id == "D-0082-2027"))
    assert argatroban.severity == "critical" and len(argatroban.lots) >= 10

    again = ingest("us-fda-drug")
    assert (again.created, again.updated) == (0, 0)
    assert count(db, Alert) == 25


def test_new_alert_matches_existing_stock_and_notifies(client, ingest, db):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30mg tablets", "lot": "7717825A", "quantity": "12"})
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30mg tablets", "lot": "OTHER999"})
    post_form(
        client, "/app/inventory", {"product_name": "Paracetamol 500mg", "lot": "7717825A"}
    )  # same batch code, other product
    run = ingest("us-fda-drug")
    assert run.matched == 1
    match = db.scalar(select(Match))
    assert match.level == "batch" and match.confidence >= 90
    assert match.holding.lot == "7717825A" and "Cinacalcet" in match.holding.product_name
    assert count(db, Notification, Notification.channel == "in_app") == 1
    assert OUTBOX and "1 stock item(s) match" in OUTBOX[-1]["Subject"]

    dashboard = client.get("/app")
    assert "1 match needs action" in dashboard.text
    detail = client.get(f"/app/matches/{match.id}")
    assert "Batch 7717825A is listed in the alert" in detail.text


def test_new_stock_matches_existing_alert_immediately(client, ingest, db):
    ingest("us-fda-drug")
    signup(client)
    response = post_form(
        client,
        "/app/inventory",
        {"product_name": "Telmisartan and Amlodipine tablets 40mg/5mg", "lot": "3228114", "manufacturer": "Mylan"},
    )
    assert response.headers["location"] == "/app/matches"
    match = db.scalar(select(Match))
    assert match is not None and match.level == "batch"


def test_resolving_a_match_updates_outcome_metrics(client, ingest, db):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30 mg", "lot": "7717825A"})
    ingest("us-fda-drug")
    match = db.scalar(select(Match))
    invalid = post_form(client, f"/app/matches/{match.id}/resolve", {"outcome": "teleported", "units_affected": "lots"})
    assert invalid.status_code == 422
    done = post_form(
        client, f"/app/matches/{match.id}/resolve", {"outcome": "quarantined", "units_affected": "12", "note": "Shelf B2"}
    )
    assert done.status_code == 303
    db.refresh(match)
    assert (match.status, match.outcome, match.units_affected) == ("resolved", "quarantined", 12)
    impact = client.get("/impact")
    assert impact.status_code == 200
    assert ">12<" in impact.text  # units removed
    reopened = post_form(client, f"/app/matches/{match.id}/reopen")
    assert reopened.status_code == 303
    db.refresh(match)
    assert match.status == "open"


def test_mhra_ingest_reads_batch_table(ingest, db):
    run = ingest("uk-mhra")
    assert run.status == "succeeded"
    alert = db.scalar(select(Alert).where(Alert.title.like("Class 3 Medicines Recall: Aspire%")))
    assert alert.severity == "moderate" and alert.manufacturer == "Aspire Pharma Ltd"
    assert [lot.lot for lot in alert.lots] == ["E941A", "E941B", "E941C"]
    assert alert.active_ingredients == ["tropicamide"]
    assert "5060209733661" in alert.product_codes
    # weekly digests and roundups are not alerts
    assert count(db, Alert, Alert.title.like("Field Safety Notices%")) == 0
    assert count(db, Alert, Alert.title.like("MHRA Safety Roundup%")) == 0


def test_health_canada_ingest_filters_and_enriches(ingest, db):
    run = ingest("ca-health-canada")
    assert run.status == "succeeded"
    alert = db.scalar(select(Alert).where(Alert.title.like("Cyclophosphamide%")))
    assert alert.severity == "critical"  # Type I
    assert [lot.lot for lot in alert.lots] == ["7Z10346A"]
    assert alert.manufacturer == "Marcan Pharmaceuticals Inc"
    assert "02560216" in alert.product_codes
    assert count(db, Alert, Alert.raw["Organization"].astext == "TC") == 0  # transport recalls skipped


def test_who_ingest_creates_product_level_falsified_alerts(ingest, db):
    run = ingest("who-mpa")
    assert run.status == "succeeded"
    darzalex = db.scalar(select(Alert).where(Alert.title.like("%DARZALEX%")))
    assert darzalex.alert_type == "falsified" and darzalex.severity == "critical"
    assert darzalex.product_names == ["DARZALEX"] and darzalex.active_ingredients == ["daratumumab"]
    assert darzalex.country_code is None


def test_falsified_product_alert_matches_brand_stock_at_product_level(client, ingest, db):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Darzalex 400mg/20ml", "lot": "AB12345"})
    ingest("who-mpa")
    match = db.scalar(select(Match))
    assert match is not None and match.level == "product"
    assert "check your batch in the original notice" in match.reasons[0]


def test_failed_run_is_recorded(ingest, db):
    try:
        ingest("us-fda-drug", fail={"api.fda.gov"})
    except Exception:  # noqa: BLE001
        pass
    run = db.scalar(select(IngestionRun).order_by(IngestionRun.id.desc()))
    assert run.status == "failed" and "503" in run.error
    source = db.get(Source, "us-fda-drug")
    db.refresh(source)
    assert source.last_error and source.last_success_at is None


def test_archived_holdings_are_not_matched(client, ingest, db):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30mg", "lot": "7717825A"})
    from batchlight.models import Holding

    holding = db.scalar(select(Holding))
    post_form(client, f"/app/inventory/{holding.id}/archive")
    ingest("us-fda-drug")
    assert count(db, Match) == 0
    assert count(db, AlertLot) > 0


def test_health_canada_summary_refresh_keeps_page_details(ingest, db, monkeypatch):
    from batchlight.config import get_settings

    ingest("ca-health-canada")
    alert = db.scalar(select(Alert).where(Alert.title.like("Cyclophosphamide%")))
    assert alert.lot_count == 1 and alert.raw["detail_fetched"] is True
    # Next run fetches no pages at all: the stored batches and company must survive.
    monkeypatch.setattr(get_settings(), "health_canada_detail_limit", 0)
    again = ingest("ca-health-canada")
    assert again.updated == 0
    db.expire_all()
    alert = db.scalar(select(Alert).where(Alert.title.like("Cyclophosphamide%")))
    assert [lot.lot for lot in alert.lots] == ["7Z10346A"] and alert.manufacturer == "Marcan Pharmaceuticals Inc"


def test_correcting_a_batch_number_clears_the_stale_match(client, ingest, db):
    from batchlight.models import Holding

    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30 mg", "lot": "7717825A", "manufacturer": "Strides"})
    ingest("us-fda-drug")
    assert count(db, Match) == 1
    holding = db.scalar(select(Holding))
    post_form(
        client,
        f"/app/inventory/{holding.id}/edit",
        {"product_name": "Cinacalcet 30 mg", "lot": "7717826B", "manufacturer": "Cipla"},
    )
    assert count(db, Match) == 0


def test_archiving_removes_open_matches_but_keeps_resolved_history(client, ingest, db):
    from batchlight.models import Holding

    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30 mg", "lot": "7717825A"})
    post_form(client, "/app/inventory", {"product_name": "Heparin Sodium Injection", "lot": "CU03232A"})
    ingest("us-fda-drug")
    cinacalcet_id, heparin_id = (
        db.scalar(select(Match.id).join(Holding).where(Holding.product_name.like(f"{name}%")))
        for name in ("Cinacalcet", "Heparin")
    )
    post_form(client, f"/app/matches/{heparin_id}/resolve", {"outcome": "returned"})
    for holding in db.scalars(select(Holding)).all():
        post_form(client, f"/app/inventory/{holding.id}/archive")
    db.expire_all()
    assert db.get(Match, cinacalcet_id) is None
    assert db.get(Match, heparin_id).status == "resolved"


def test_alerts_saved_by_a_failed_run_are_matched_by_the_next_run(client, ingest, db, monkeypatch):
    import batchlight.matching

    signup(client)
    post_form(client, "/app/inventory", {"product_name": "Cinacalcet 30 mg", "lot": "7717825A"})
    real = batchlight.matching.match_alerts

    def broken(*args, **kwargs):
        raise RuntimeError("matching crashed")

    monkeypatch.setattr(batchlight.matching, "match_alerts", broken)
    try:
        ingest("us-fda-drug")
    except RuntimeError:
        pass
    assert count(db, Alert) == 25 and count(db, Match) == 0
    monkeypatch.setattr(batchlight.matching, "match_alerts", real)
    run = ingest("us-fda-drug")
    assert run.created == 0 and run.matched == 1


def test_automated_records_default_to_official(ingest, db):
    for source_id in ("us-fda-drug", "us-fda-device", "us-fda-food", "uk-mhra", "ca-health-canada", "ca-cfia-food", "who-mpa"):
        assert ingest(source_id).status == "succeeded", source_id
    assert count(db, Alert) > 0
    assert set(db.scalars(select(Alert.evidence).distinct())) == {"official"}


def test_content_hash_unchanged_by_official_default():
    import hashlib
    import json
    from dataclasses import asdict
    from datetime import date

    from batchlight.ingest.base import AlertRecord

    record = AlertRecord(
        external_id="D-1",
        title="Cinacalcet Tablets recall",
        severity="high",
        alert_type="recall",
        source_url="https://www.fda.gov/",
        published_at=date(2026, 10, 1),
    )
    assert record.evidence == "official"
    legacy = asdict(record)
    for key in ("raw", "partial", "evidence"):  # the payload hashed before the evidence field existed
        legacy.pop(key)
    assert record.content_hash() == hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()
    record.evidence = "report"
    assert record.content_hash() != hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()


def test_who_alert_keeps_all_manufacturers():
    from datetime import date

    from batchlight.ingest.who import map_page
    from tests.conftest import fixture_text

    url = "https://www.who.int/news/item/13-10-2025-medical-product-alert-n-5-2025--substandard-(contaminated)-oral-liquid-medicines"
    record = map_page(url, fixture_text("who_alert_oral_liquids.html"), date(2025, 10, 13))
    assert record.manufacturer == "Sresan Pharmaceutical, Rednex Pharmaceuticals, Shape Pharma"
    assert record.evidence == "official"
