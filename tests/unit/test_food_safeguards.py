"""Regression tests for the Food Safety review: privacy, proxy handling, source labelling and CFIA text repair."""

from types import SimpleNamespace

import pytest

from batchlight.food import reports
from batchlight.food.knowledge import EVIDENCE_LABELS, TESTING_FACTS, TOPICS
from batchlight.ingest.cfia import restore_sentences
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
    findings = list(TESTING_FACTS) + [f for topic in TOPICS for f in topic.findings]
    assert {f.evidence for f in findings} <= set(EVIDENCE_LABELS)
    price = next(f for f in TESTING_FACTS if "pjtau" in f.url)
    assert price.evidence == "provider" and "PJTAU" in price.text
    hosted = next(f for f in TESTING_FACTS if "legalitysimplified" in f.url)
    assert "copy hosted" in hosted.source


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
