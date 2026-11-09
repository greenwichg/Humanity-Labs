"""Regression tests for the Food Safety review: privacy, proxy handling, source labelling and CFIA text repair."""

import json
import re
from types import SimpleNamespace

import pytest

from batchlight.food import reports
from batchlight.food.knowledge import EVIDENCE_HELP, EVIDENCE_LABELS, TESTING_FACTS, TOPICS
from batchlight.ingest.cfia import restore_sentences
from batchlight.services.curation import CuratedAlertInput
from batchlight.services.india_seed import DATA as INDIA_SEED
from batchlight.web import auth
from batchlight.web.templating import source_host, source_kind


def test_ipv6_addresses_in_one_64_share_a_fingerprint():
    a = reports.ip_fingerprint("2001:db8:abcd:12::1")
    b = reports.ip_fingerprint("2001:db8:abcd:12:ffff:ffff:ffff:fffe")
    c = reports.ip_fingerprint("2001:db8:abcd:13::1")
    assert a == b != c
    assert reports.ip_fingerprint("::ffff:203.0.113.9") == reports.ip_fingerprint("203.0.113.9")
    assert reports.ip_fingerprint("203.0.113.9") != reports.ip_fingerprint("203.0.113.10")
    assert reports.ip_fingerprint(None) is None
    assert "203.0.113.9" not in reports.ip_fingerprint("203.0.113.9")


@pytest.mark.parametrize(
    ("trusted", "header", "expected"),
    [
        (True, "198.51.100.7, 203.0.113.9", "203.0.113.9"),  # the client can't choose its rate-limit bucket
        (True, None, "10.0.0.2"),
        (False, "198.51.100.7, 203.0.113.9", "10.0.0.2"),
    ],
)
def test_client_ip_uses_the_entry_our_proxy_appended(monkeypatch, trusted, header, expected):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(trust_proxy_headers=trusted))
    request = SimpleNamespace(headers={"x-forwarded-for": header} if header else {}, client=SimpleNamespace(host="10.0.0.2"))
    assert auth.client_ip(request) == expected


def test_source_kind_separates_regulator_notices_from_reports():
    assert source_kind("https://www.fssai.gov.in/upload/advisories/x.pdf") == "official"
    assert source_kind("https://recalls-rappels.canada.ca/en/alert-recall/x") == "official"
    assert source_kind("https://www.fda.gov/safety/recalls") == "official"
    assert source_kind("https://www.cfs.gov.hk/english/press/x.html") == "official"
    assert source_kind("https://theprint.in/india/x") == "report"
    assert source_kind("https://notgov.example/x") == "report"
    assert source_kind(None) == "report"
    assert source_host("https://www.businesstoday.in/x") == "businesstoday.in"


def test_every_finding_uses_a_known_evidence_label():
    assert EVIDENCE_LABELS["official"] == "Official source"
    assert EVIDENCE_LABELS["lab_test"] == "Official lab result"
    assert EVIDENCE_LABELS["report"] == "News or NGO report"
    assert EVIDENCE_LABELS["study"] == "Peer-reviewed study"
    assert EVIDENCE_LABELS["provider"] == "Provider's own information"
    assert set(EVIDENCE_HELP) == set(EVIDENCE_LABELS)
    assert "Official data" not in EVIDENCE_LABELS.values()
    findings = list(TESTING_FACTS) + [f for topic in TOPICS for f in topic.findings]
    assert {f.evidence for f in findings} <= set(EVIDENCE_LABELS)
    price = next(f for f in TESTING_FACTS if "pjtau" in f.url)
    assert price.evidence == "provider" and "PJTAU" in price.text
    for fact in TESTING_FACTS:
        if fact.evidence == "official":
            assert source_kind(fact.url) == "official", fact.url
    assert not any("legalitysimplified" in f.url for f in TESTING_FACTS)


def seed_rows() -> list[dict]:
    return json.loads(INDIA_SEED.read_text(encoding="utf-8"))


def seed_row(word: str) -> dict:
    """The one seed row whose title mentions ``word``."""
    rows = [row for row in seed_rows() if word.lower() in row["title"].lower()]
    assert len(rows) == 1, (word, [row["title"] for row in rows])
    return rows[0]


def is_who_host(url: str) -> bool:
    host = source_host(url)
    return host == "who.int" or host.endswith(".who.int")


def test_seed_rows_declare_evidence_and_match_link():
    rows = seed_rows()
    assert rows
    for row in rows:
        data = CuratedAlertInput(**row)
        assert data.evidence == row["evidence"]
        if data.evidence in ("official", "lab_test"):
            assert source_kind(data.source_url) == "official", data.title

    assert seed_row("Everest Cumin")["evidence"] == "report"
    assert seed_row("Patanjali")["evidence"] == "report"
    hong_kong = seed_row("Hong Kong")
    assert hong_kong["evidence"] == "lab_test" and source_host(hong_kong["source_url"]) == "cfs.gov.hk"
    korova = seed_row("Korova")
    assert korova["evidence"] == "lab_test" and source_host(korova["source_url"]) == "fssai.gov.in"
    assert seed_row("SDP Industries")["evidence"] == "report"
    coldrif = seed_row("Coldrif")
    assert coldrif["evidence"] == "lab_test" and source_host(coldrif["source_url"]) == "sansad.in"
    for word in ("Respifresh TR", "ReLife"):
        row = seed_row(word)
        assert row["evidence"] == "lab_test" and is_who_host(row["source_url"]), word


def test_seed_rows_have_no_unsupported_claims():
    everest = seed_row("Everest Cumin")
    assert not re.search(r"only (to )?(this|that) batch", everest["reason"], re.I)
    assert "referral food laboratory" in everest["reason"]

    hong_kong = seed_row("Hong Kong")
    assert "Hong Kong" in hong_kong["title"] and "samples" in hong_kong["title"]
    assert "All these dates have passed" in hong_kong["reason"]

    assert "28 Indian test reports" not in INDIA_SEED.read_text(encoding="utf-8")

    patanjali = seed_row("Patanjali")
    assert patanjali["batches"] == ""
    assert "AJD2400012" in patanjali["reason"] and re.search(r"\bJD2400012\b", patanjali["reason"])

    assert seed_row("SDP Industries")["reason"].startswith("Check the maker first")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("recalled productsDo not consume", "recalled products. Do not consume"),
        ("McCain Foods (Canada) recalls fries", "McCain Foods (Canada) recalls fries"),
        ("PepsiCo Canada ULC", "PepsiCo Canada ULC"),
        ("", ""),
    ],
)
def test_restore_sentences_leaves_company_names_alone(text, expected):
    assert restore_sentences(text) == expected
