"""Public infrastructure: parsers, regions, classification and Project Passport facts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from batchlight.infra.classify import classify
from batchlight.infra.geo import REGIONS, find_regions
from batchlight.infra.sources import add_duration, map_wb_notice, map_wb_project, notice_text, parse_award, parse_cag_listing
from batchlight.infra.store import party_key

FIX = Path(__file__).parent.parent / "fixtures"


def notices(name):
    return json.loads((FIX / name).read_text())["procnotices"]


def test_all_36_states_and_union_territories_are_listed():
    assert len(REGIONS) == 36
    assert sum(r.kind == "state" for r in REGIONS) == 28
    assert len({r.slug for r in REGIONS}) == 36


@pytest.mark.parametrize(
    "text, expected",
    [
        ("IN-GUJARAT SPMU-469467-CW-RFB", ["gujarat"]),
        ("Resilient Action for Development and Disaster Recovery Himachal Pradesh", ["himachal-pradesh"]),
        ("Government of Maharasthra, Water Resources Department", ["maharashtra"]),
        ("Orissa Integrated Irrigated Agriculture", ["odisha"]),
        ("Goal-based targets for urban roads", []),
        ("Andhra Pradesh and Arunachal Pradesh", ["andhra-pradesh", "arunachal-pradesh"]),
        ("", []),
    ],
)
def test_find_regions_uses_whole_names_and_aliases(text, expected):
    assert find_regions(text) == expected


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Construction of ROB at level crossing", "bridge"),
        ("Rehabilitation of Approach Road and Improvement of Basic Facilities at Janauri Dam", "dam"),
        ("Rehabilitation of Sewer Line for western side of City", "sanitation"),
        ("Annual Maintenance Plan on Mandi road km 0/0 to 16/190", "road"),
        ("Construction of storm water drain", "drainage"),
        ("Construction of district hospital building", "building"),
        ("Supply of laptops", "other"),
    ],
)
def test_classify(text, kind):
    assert classify(text) == kind


def test_add_duration_handles_month_ends():
    assert add_duration(date(2024, 1, 31), 1, "Month") == date(2024, 2, 29)
    assert add_duration(date(2023, 9, 13), 11, "Month") == date(2024, 8, 13)
    assert add_duration(date(2026, 9, 18), 730, "Day") == date(2028, 9, 17)
    assert add_duration(date(2025, 1, 1), 2, "Year") == date(2027, 1, 1)


def test_party_key_merges_common_spellings():
    assert party_key("M/s. ABC Constructions Pvt. Ltd.") == party_key("ABC CONSTRUCTIONS PRIVATE LIMITED")
    assert party_key("MESARS JAYSHREE NEW CHAMUNDA CONSTRUCTION CO.") == "JAYSHREE NEW CHAMUNDA CONSTRUCTION COMPANY"


def test_award_notice_fields():
    award = parse_award(notice_text(notices("infra_wb_awards.json")[0]["notice_text"]))
    assert award["awarded_on"] == date(2023, 9, 13)
    assert award["duration"] == (11, "Month")
    assert award["contractors"] == ["MESARS JAYSHREE NEW CHAMUNDA CONSTRUCTION CO."]
    assert (award["currency"], award["amount"]) == ("INR", 25721854.0)
    assert award["rejected"] == 1


def test_award_becomes_a_work_with_a_calculated_completion_date():
    record = map_wb_notice(notices("infra_wb_awards.json")[0])
    assert record.level == "work" and record.parent_external_id == "P170873"
    assert record.state_slugs == ["gujarat"] and record.asset_type == "dam"
    assert record.promised_completion_on == date(2024, 8, 13) and record.promised_basis == "calculated"
    assert record.source_url.endswith("/procurement-detail/OP00478940")
    kinds = {e.kind: e for e in record.events}
    assert kinds["award"].evidence == "official" and "25,721,854.00" in kinds["award"].title
    assert kinds["plan"].evidence == "calculated" and "does not give a completion date" in kinds["plan"].detail
    # Rejected bidders are counted, never named.
    assert "HARSIDDHI" not in json.dumps([e.detail for e in record.events])


def test_tender_notice_keeps_only_a_real_deadline():
    records = [map_wb_notice(n) for n in notices("infra_wb_tenders.json")]
    punjab = next(r for r in records if "PUNJAB" in r.external_id)
    assert punjab.state_slugs == ["punjab"] and punjab.tendering_body == "Punjab Water Resources Division"
    assert punjab.bid_deadline_on > punjab.tendered_on
    same_day = [r for r in records if "HP" in r.external_id]
    assert all(r.bid_deadline_on is None for r in same_day)  # deadline equal to the notice date is a data error


def test_programme_record():
    projects = json.loads((FIX / "infra_wb_projects.json").read_text())["projects"]
    drip = map_wb_project(projects["P170873"])
    assert drip.level == "programme" and drip.asset_type == "dam"
    assert "kerala" in drip.state_slugs and len(drip.state_slugs) > 5
    assert drip.promised_basis == "financing_close"
    assert next(e for e in drip.events if e.kind == "plan").detail.endswith("not a completion date for the works.")


def test_cag_listing_keeps_public_works_reports_with_states():
    docs = parse_cag_listing((FIX / "infra_cag_listing.html").read_text())
    assert docs and all(d.url.startswith("https://cag.gov.in/en/audit-report/details/") for d in docs)
    assert not any("test" == d.title.lower() for d in docs)
    west_bengal = next(d for d in docs if "West Bengal" in d.title)
    assert west_bengal.state_slugs == ["west-bengal"] and "Transport" in west_bengal.sectors
    assert not any("Direct Tax" in d.title for d in docs)  # not about public works
