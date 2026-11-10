"""Public infrastructure: ingestion from saved official records, change tracking and the citizen pages."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

FIX = Path(__file__).parent.parent / "fixtures"


def transport(awards=None):
    projects = json.loads((FIX / "infra_wb_projects.json").read_text())
    award_notices = awards or json.loads((FIX / "infra_wb_awards.json").read_text())["procnotices"]
    tenders = json.loads((FIX / "infra_wb_tenders.json").read_text())["procnotices"]
    cag = (FIX / "infra_cag_listing.html").read_text()
    pmgsy = (FIX / "infra_pmgsy_sample.csv").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.worldbank.org" and request.url.path.endswith("/projects"):
            return httpx.Response(200, json=projects)
        if request.url.host == "search.worldbank.org" and request.url.path.endswith("/procnotices"):
            items = award_notices + tenders
            return httpx.Response(200, json={"total": len(items), "procnotices": items})
        if request.url.host == "cag.gov.in":
            return httpx.Response(200, text=cag)
        if request.url.host == "www.mospi.gov.in" and request.method == "POST":
            title = "<p>Monthly Flash Report on Infrastructure Projects worth Rs. 150 crore and above - September 2026</p>"
            return httpx.Response(
                200, json={"data": [{"title": title, "published_year": "2026-10-26", "file_one": {"path": "uploads/fr.pdf"}}]}
            )
        if request.url.host == "www.mospi.gov.in":
            return httpx.Response(200, content=b"%PDF-1.4 stand-in", headers={"content-type": "application/pdf"})
        if request.url.host == "ckandev.indiadataportal.com":
            return httpx.Response(200, content=pmgsy, headers={"content-type": "text/csv"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def mospi_pages(change=None):
    from batchlight.infra.mospi import Page

    pages = json.loads((FIX / "infra_mospi_flash_pages.json").read_text())
    if change:
        for word in pages[1]["words"]:
            if word["text"] == change[0]:
                word["text"] = change[1]
    return [Page(**p) for p in pages]


@pytest.fixture
def ingested(db, monkeypatch):
    from batchlight.db import session_factory
    from batchlight.infra import mospi
    from batchlight.infra.sources import REGISTRY
    from batchlight.infra.store import run_infra_source

    monkeypatch.setattr(mospi, "read_pages", lambda _bytes: mospi_pages())
    REGISTRY["infra-cag-reports"].pages = 1
    client = httpx.Client(transport=transport())
    runs = {sid: run_infra_source(session_factory(), adapter, client) for sid, adapter in REGISTRY.items()}
    REGISTRY["infra-cag-reports"].pages = 3
    return runs


def project(db, external_id):
    from sqlalchemy import select

    from batchlight.models import InfraProject

    db.expire_all()
    return db.scalar(select(InfraProject).where(InfraProject.external_id == external_id))


def test_ingestion_builds_linked_passports_and_is_idempotent(db, ingested):
    from batchlight.db import session_factory
    from batchlight.infra.sources import REGISTRY
    from batchlight.infra.store import run_infra_source

    assert all(run.status == "succeeded" for run in ingested.values())
    work = project(db, "IN-GUJARAT SPMU-469467-CW-RFB")
    programme = project(db, "P170873")
    assert work.parent_id == programme.id
    assert work.contractors_text == "MESARS JAYSHREE NEW CHAMUNDA CONSTRUCTION CO."
    # A work that names no state takes it from a single-state programme.
    sewer = project(db, "IN-AMC-524150-CW-RFP")
    assert sewer.state_slugs == ["gujarat"]
    again = run_infra_source(session_factory(), REGISTRY["infra-wb-civil-works"], httpx.Client(transport=transport()))
    assert (again.created, again.updated) == (0, 0)


def test_a_changed_contract_price_is_recorded_as_a_change(db, ingested):
    from batchlight.db import session_factory
    from batchlight.infra.sources import REGISTRY
    from batchlight.infra.store import run_infra_source

    awards = json.loads((FIX / "infra_wb_awards.json").read_text())["procnotices"]
    changed = copy.deepcopy(awards)
    changed[0]["notice_text"] = changed[0]["notice_text"].replace("25721854.00", "26000000.00")
    run = run_infra_source(session_factory(), REGISTRY["infra-wb-civil-works"], httpx.Client(transport=transport(changed)))
    assert run.updated == 1
    work = project(db, "IN-GUJARAT SPMU-469467-CW-RFB")
    change = next(e for e in work.events if e.kind == "change")
    assert change.title == "Contract price changed: from ₹2.57 crore to ₹2.60 crore"


def test_passport_shows_sources_labels_and_missing_records(client, db, ingested):
    work = project(db, "IN-GUJARAT SPMU-469467-CW-RFB")
    page = client.get(f"/infrastructure/projects/{work.id}").text
    assert "Project Passport" in page and "Gujarat" in page
    assert "₹2.57 crore" in page and "MESARS JAYSHREE NEW CHAMUNDA CONSTRUCTION CO." in page
    assert "Calculated by us" in page and "The notice gives no completion date" in page
    assert "Not published, or not in our sources yet" in page
    assert "Completion certificates are not published" in page and "rtionline.gov.in" in page
    assert "that alone does not show a delay" in page  # the calculated date has passed
    assert "HARSIDDHI" not in page  # rejected bidders are never named
    assert "procurement-detail/OP00478940" in page
    for word in ("corrupt", "scam", "fraud"):
        assert word not in page.lower()


def test_programme_passport_lists_its_works_and_explains_the_closing_date(client, db, ingested):
    programme = project(db, "P170873")
    page = client.get(f"/infrastructure/projects/{programme.id}").text
    assert "Contracted works in this programme" in page and "Ukai" in page
    assert "not a completion date for the works" in page
    assert "US$713.4 million" in page


def test_home_lists_every_state_and_ut_and_explains_coverage(client, ingested):
    page = client.get("/infrastructure").text
    assert page.count('href="/infrastructure/states/') == 36
    assert "PMGSY rural roads" in page and "so many public works are not here" in page
    assert "not that nothing was built" in page


def test_search_state_contractor_and_sources_pages(client, db, ingested):
    from sqlalchemy import select

    from batchlight.models import InfraParty

    assert "Mandi" in client.get("/infrastructure/projects?q=Mandi").text
    himachal = client.get("/infrastructure/states/himachal-pradesh").text
    assert "Public works in Himachal Pradesh" in himachal and "e-procurement portal" in himachal
    assert client.get("/infrastructure/states/atlantis").status_code == 404
    west_bengal = client.get("/infrastructure/states/west-bengal").text
    assert "Compliance Audit Report of Government of West Bengal" in west_bengal
    party = db.scalar(select(InfraParty).where(InfraParty.name == "VIJAY KUMAR"))
    contractor = client.get(f"/infrastructure/contractors/{party.id}").text
    assert "Winning contracts is not evidence of anything wrong" in contractor and "₹29.49 lakh" in contractor
    sources = client.get("/infrastructure/sources").text
    assert "PMGSY rural roads (India Data Portal copy of OMMAS data)" in sources and "own portal (OMMAS)" in sources
    assert "Checked, not connected yet" in sources


def test_medicine_and_food_pages_are_unaffected(client, ingested):
    for path in ("/", "/medicines", "/check?batch=SR-13", "/food", "/sources"):
        assert client.get(path).status_code == 200
    assert "infra-" not in client.get("/sources").text


def test_pmgsy_roads_become_passports_with_republished_labels_and_no_unconfirmed_costs(client, db, ingested):
    from sqlalchemy import func, select

    from batchlight.models import InfraProject

    assert ingested["infra-pmgsy-roads"].status == "succeeded"
    roads = db.scalars(select(InfraProject).where(InfraProject.source_id == "infra-pmgsy-roads")).all()
    assert len(roads) == 8  # 10 rows: one road serves three habitations
    kerala = next(r for r in roads if r.state_slugs == ["kerala"])
    page = client.get(f"/infrastructure/projects/{kerala.id}").text
    assert "Official data, republished" in page and "India Data Portal" in page and "omms.nic.in" in page
    assert "Joseph Thomas" in page and "11 Nov 2015" in page  # contractor and completion
    assert "Areekara" in page and "Kozhuvalloor" in page and "Pottakulam" in page
    assert "Chengannur block, Alappuzha district, Kerala" in page
    assert "does not state their unit" in page and "₹" not in page.split("What the records show")[1].split("Project history")[0]
    assert "“Maintenance”" in page
    building = next(r for r in roads if r.status == "Sub-Grade Stage")
    assert building.actual_completion_on is None  # a date on an unfinished road is not shown as completion
    assert "Kerala" in client.get("/infrastructure/states/kerala").text
    total = db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.source_id == "infra-pmgsy-roads"))
    from batchlight.db import session_factory
    from batchlight.infra.sources import REGISTRY
    from batchlight.infra.store import run_infra_source

    again = run_infra_source(session_factory(), REGISTRY["infra-pmgsy-roads"], httpx.Client(transport=transport()))
    assert (again.created, again.updated) == (0, 0)
    assert db.scalar(select(func.count()).select_from(InfraProject).where(InfraProject.source_id == "infra-pmgsy-roads")) == total


def test_central_projects_from_mospi_show_official_figures_and_labelled_changes(client, db, ingested):
    assert ingested["infra-mospi-central"].status == "succeeded"
    vijayawada = project(db, "MOSPI-701107")
    page = client.get(f"/infrastructure/projects/{vijayawada.id}").text
    assert "Central project" in page and "Andhra Pradesh" in page and "Airport Authority of India [AAI]" in page
    assert "₹611.80 crore" in page and "₹824.28 crore" in page and "₹618.57 crore" in page
    assert "It has risen by ₹212.48 crore (35%) since approval." in page and "Moved later by 50 months." in page
    assert "Calculated by us" in page and "Figures as of September 2026" in page
    assert "Jun 2020" in page and "1 Jun 2020" not in page  # the report gives months, not days
    assert "The report does not name contractors." in page
    state = client.get("/infrastructure/states/andhra-pradesh").text
    assert "Large central projects in Andhra Pradesh" in state
    assert "Vijayawada" in client.get("/infrastructure/projects?level=project&q=Vijayawada").text
    assert "central projects of ₹150 crore and above" in client.get("/infrastructure").text


def test_a_revised_central_cost_is_recorded_as_a_change(db, ingested, monkeypatch):
    from batchlight.db import session_factory
    from batchlight.infra import mospi
    from batchlight.infra.sources import REGISTRY
    from batchlight.infra.store import run_infra_source

    monkeypatch.setattr(mospi, "read_pages", lambda _bytes: mospi_pages(change=("(824.28)", "(900.00)")))
    run = run_infra_source(session_factory(), REGISTRY["infra-mospi-central"], httpx.Client(transport=transport()))
    assert run.updated >= 1
    change = next(e for e in project(db, "MOSPI-701107").events if e.kind == "change")
    assert change.title == "Total cost changed: from ₹824.28 crore to ₹900.00 crore"


def test_contractor_directory_and_profile_use_only_official_records(client, db, ingested):
    from sqlalchemy import select

    from batchlight.models import InfraParty

    directory = client.get("/infrastructure/contractors?q=Joseph").text
    assert "Joseph Thomas" in directory and "not evidence of anything wrong" in directory
    party = db.scalar(select(InfraParty).where(InfraParty.name == "Joseph Thomas"))
    profile = client.get(f"/infrastructure/contractors/{party.id}").text
    assert "Works awarded" in profile and "Kerala" in profile and "Maintenance: <strong>1</strong>" in profile
    assert "about <strong>12.0 months</strong>" in profile and "Calculated by us" in profile
    assert "It does not show whether any work was late" in profile
    assert "Construction quality, inspection grades, penalties and debarment are not in our sources" in profile
    assert "Main contractor" in profile and "Official data, republished" in profile  # history table with evidence
    assert client.get("/infrastructure/contractors?page=2").status_code == 200


def test_a_shared_name_in_another_source_or_state_is_never_merged(client, db, ingested):
    from sqlalchemy import select

    from batchlight.infra.sources import ProjectData
    from batchlight.infra.store import party_key, upsert_project
    from batchlight.models import InfraParty, Source

    pmgsy = db.get(Source, "infra-pmgsy-roads")
    record = ProjectData(
        external_id="PMGSY-TEST-1",
        level="work",
        title="Test road",
        source_url="https://ckandev.indiadataportal.com/dataset/pradhan-mantri-gram-sadak-yojana-pmgsy",
        state_slugs=["bihar"],
        state_basis="source",
        contractors=["Vijay Kumar"],
        data={"evidence": "official_copy", "scheme": "PMGSY"},
    )
    upsert_project(db, pmgsy, record)
    db.commit()
    parties = db.scalars(
        select(InfraParty).where(InfraParty.key == party_key("VIJAY KUMAR"), InfraParty.kind == "contractor")
    ).all()
    assert {(p.scope_source, p.scope_state) for p in parties} == {
        ("infra-wb-civil-works", "himachal-pradesh"),
        ("infra-pmgsy-roads", "bihar"),
    }
    himachal = next(p for p in parties if p.scope_state == "himachal-pradesh")
    page = client.get(f"/infrastructure/contractors/{himachal.id}").text
    assert "Contractor as named in World Bank civil-works tenders and contract awards in India, Himachal Pradesh" in page
    assert "The same name in other records" in page and "Bihar" in page and "we keep them apart" in page
    assert "Test road" not in page  # the Bihar record stays on its own page


def test_passports_name_owner_agency_tendering_body_and_contractor_only_from_sources(client, db, ingested):
    central = client.get(f"/infrastructure/projects/{project(db, 'MOSPI-701107').id}").text
    who = central.split("Who is involved")[1].split("</dl>")[0]
    assert "Owner (reporting ministry)" in who and "Ministry of Civil Aviation" in who
    assert "Implementing agency" in who and "Airport Authority of India [AAI]" in who
    assert "The report does not name contractors." in who and "We never guess them." in who
    tender = client.get(f"/infrastructure/projects/{project(db, 'IN-PUNJAB WRD-574921-CW-RFB').id}").text
    who = tender.split("Who is involved")[1].split("</dl>")[0]
    assert "Tendering body" in who and "Punjab Water Resources Division" in who
    assert "No contractor is named in the records we have." in who  # tender only, no award yet
    award = client.get(f"/infrastructure/projects/{project(db, 'IN-GUJARAT SPMU-469467-CW-RFB').id}").text
    who = award.split("Who is involved")[1].split("</dl>")[0]
    assert "Main contractor" in who and "MESARS JAYSHREE NEW CHAMUNDA CONSTRUCTION CO." in who
    assert "Named as the awarded bidder in the contract award notice." in who
    assert "the record does not say which agency ran this work" in who


def test_party_scope_migration_round_trip_keeps_every_contractor_link(db, ingested):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from batchlight.db import get_engine

    def contractor_links():
        with get_engine().connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*), count(DISTINCT p.key) FROM infra_project_parties pp "
                    "JOIN infra_parties p ON p.id = pp.party_id WHERE pp.role = 'contractor'"
                )
            ).one()

    before = contractor_links()
    db.close()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(config, "0010_infra_central_projects")
    try:
        get_engine().dispose()
        command.upgrade(config, "head")
        get_engine().dispose()
        assert contractor_links() == before
        with get_engine().connect() as conn:
            unscoped = conn.execute(
                text("SELECT count(*) FROM infra_parties WHERE kind = 'contractor' AND scope_source = ''")
            ).scalar()
        assert unscoped == 0
    finally:
        command.upgrade(config, "head")
        get_engine().dispose()
