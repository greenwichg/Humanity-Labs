"""Shared fixtures. Integration tests use a real PostgreSQL database (BATCHLIGHT_TEST_DATABASE_URL)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

TEST_DB = os.environ.get("BATCHLIGHT_TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/batchlight_test")
os.environ.update(
    {
        "BATCHLIGHT_ENV": "test",
        "BATCHLIGHT_DATABASE_URL": TEST_DB,
        "BATCHLIGHT_EMAIL_BACKEND": "console",
        "BATCHLIGHT_BASE_URL": "http://testserver",
        "BATCHLIGHT_INGEST_SINCE": "2026-01-01",
        "BATCHLIGHT_LOGIN_MAX_FAILURES": "4",
    }
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


@pytest.fixture(scope="session")
def database():
    from sqlalchemy import create_engine, text

    from batchlight.config import get_settings
    from batchlight.db import reset_engine

    get_settings.cache_clear()
    reset_engine()
    engine = create_engine(TEST_DB)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(Path(__file__).parent.parent / "alembic.ini")), "head")
    yield
    reset_engine()


@pytest.fixture
def db(database):
    from sqlalchemy import text

    from batchlight.db import get_engine, session_factory
    from batchlight.services.email import OUTBOX

    with get_engine().begin() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
            )
        ]
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    OUTBOX.clear()
    session = session_factory()()
    from batchlight.ingest import ensure_sources

    ensure_sources(session)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def app(db):
    from batchlight.web.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_client(app):
    """Factory for independent browser sessions."""
    from fastapi.testclient import TestClient

    clients = []

    def factory():
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def csrf(client) -> str:
    if "bl_csrf" not in client.cookies:
        client.get("/login")
    return client.cookies["bl_csrf"]


def post_form(client, url: str, data: dict | None = None, **kwargs):
    payload = {"csrf_token": csrf(client), **(data or {})}
    return client.post(url, data=payload, follow_redirects=False, **kwargs)


def signup(
    client,
    email="owner@example.org",
    password="correct horse battery",
    org="Amina Pharmacy",
    kind="pharmacy",
    country="NG",
    name="Amina Bello",
):
    response = post_form(
        client,
        "/signup",
        {"full_name": name, "email": email, "password": password, "org_name": org, "org_kind": kind, "country_code": country},
    )
    assert response.status_code == 303, response.text[:500]
    return response


# --------------------------------------------------------------------------- mock regulator HTTP


def regulator_transport(fail: set[str] | None = None) -> httpx.MockTransport:
    """Serve recorded regulator responses. Hosts in ``fail`` answer 503."""
    fail = fail or set()
    fda_drug = fixture_json("fda_drug_enforcement.json")
    fda_device = fixture_json("fda_device_enforcement.json")
    fda_food = fixture_json("fda_food_enforcement.json")
    mhra_search = fixture_json("mhra_search.json")
    mhra_content = fixture_json("mhra_content_tropicamide.json")
    hc_open = fixture_json("hc_open_data.json")

    def handler(request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        if url.hostname in fail:
            return httpx.Response(503, text="unavailable")
        params = parse_qs(url.query)
        if url.hostname == "api.fda.gov":
            search = params.get("search", [""])[0]
            if int(params.get("skip", ["0"])[0]) > 0 or "TO 2026" not in search.replace("+", " "):
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            if "/food/" in url.path:
                return httpx.Response(200, json=fda_food)
            return httpx.Response(200, json=fda_drug if "/drug/" in url.path else fda_device)
        if url.hostname == "www.gov.uk":
            if url.path == "/api/search.json":
                start = int(params.get("start", ["0"])[0])
                body = dict(mhra_search, results=mhra_search["results"] if start == 0 else [])
                return httpx.Response(200, json=body)
            if url.path.startswith("/api/content/drug-device-alerts/class-3-medicines-recall-aspire"):
                return httpx.Response(200, json=mhra_content)
            if url.path.startswith("/api/content/"):
                link = url.path.removeprefix("/api/content")
                title = next((r["title"] for r in mhra_search["results"] if r["link"] == link), "Alert")
                return httpx.Response(
                    200,
                    json={
                        "title": title,
                        "description": "Recorded test alert",
                        "first_published_at": "2026-10-29T10:00:00Z",
                        "details": {"body": "<p>No table.</p>", "metadata": {"issued_date": "2026-10-29"}},
                    },
                )
        if url.hostname == "recalls-rappels.canada.ca":
            if url.path.endswith("HCRSAMOpenData.json"):
                return httpx.Response(200, json=hc_open)
            if "cyclophosphamide" in url.path:
                return httpx.Response(200, text=fixture_text("hc_detail_cyclophosphamide.html"))
            if "surimi" in url.path:
                return httpx.Response(200, text=fixture_text("cfia_detail_surimi.html"))
            return httpx.Response(200, text="<html><body><p>No table</p></body></html>")
        if url.hostname == "www.who.int":
            if "full-list-of-who-medical-product-alerts" in url.path:
                return httpx.Response(200, text=fixture_text("who_alert_list.html"))
            if "darzalex" in url.path:
                return httpx.Response(200, text=fixture_text("who_alert_darzalex.html"))
            if re.search(r"oral-liquid", url.path):
                return httpx.Response(200, text=fixture_text("who_alert_oral_liquids.html"))
            return httpx.Response(404)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def ingest(db):
    """Run a source adapter against recorded responses: ingest("us-fda-drug")."""
    from batchlight.db import session_factory
    from batchlight.ingest import REGISTRY, run_source
    from batchlight.ingest.base import http_client

    def run(source_id: str, fail: set[str] | None = None):
        with http_client(regulator_transport(fail)) as client:
            return run_source(session_factory(), REGISTRY[source_id], client)

    return run
