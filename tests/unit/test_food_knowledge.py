import re

from batchlight.food.knowledge import (
    COMMON_ACTIONS,
    EVIDENCE_HELP,
    EVIDENCE_LABELS,
    HOME_TESTING_GENERAL,
    TESTING_FACTS,
    TOPICS,
    TOPICS_BY_SLUG,
    WHY_IT_MATTERS,
    search_topics,
)
from batchlight.text import source_kind

FORBIDDEN = re.compile(
    r"\b(detects? adulteration|guarantee[sd]?|100% safe|is adulterated|home test (can|will) (show|prove))\b", re.I
)

EVIDENCE_KEYS = {"official", "lab_test", "report", "study", "provider"}

# Claims removed in the India-readiness audit because their source did not support them, could not be opened
# or was superseded by an official figure. None of them may come back.
REMOVED_CLAIMS = (
    "applied only to that batch",
    "applies only to that batch",
    "No accuracy data for them has been published",
    "If you need certainty",
    "28 MDH and Everest test reports",
    "6,782",
    "textile dye",
    "mainly from tartrazine",
    "600 million",
    "420,000",
    "1:1 ratio",
    "without issuing a public alert",
    "a best-before date",
)
# Links removed for the same reasons: a third-party mirror, an agency copy of a news report, a search index, a
# Rajya Sabha link that now redirects, unopenable pages, a superseded WHO estimate, an archived UK page and a
# mirror host for FSSAI files.
REMOVED_HOSTS = (
    "legalitysimplified.com",
    "latestly.com",
    "bvsalud.org",
    "rsdebate.nic.in",
    "thenewsminute.com",
    "pmc.ncbi.nlm.nih.gov/articles/4668832",
    "mail.fssai.gov.in",
    "food.gov.uk",
    "downtoearth.org.in",
)


def all_findings():
    for topic in TOPICS:
        yield from topic.findings
    yield WHY_IT_MATTERS
    yield from TESTING_FACTS


def all_text() -> str:
    parts = [HOME_TESTING_GENERAL, *COMMON_ACTIONS]
    for topic in TOPICS:
        parts += [topic.name, topic.also_called, topic.summary, topic.concern, topic.home_testing]
        parts += topic.what_you_can_do
    for finding in all_findings():
        parts += [finding.text, finding.source, finding.year, finding.url]
    return "\n".join(parts)


def test_every_finding_is_sourced_and_graded():
    for finding in all_findings():
        assert finding.url.startswith("https://"), finding.text
        assert finding.evidence in EVIDENCE_LABELS, finding.text
        assert finding.source and finding.year, finding.text
        assert len(finding.text) > 40, finding.text


def test_evidence_labels_and_help_cover_all_keys():
    assert set(EVIDENCE_LABELS) == EVIDENCE_KEYS == set(EVIDENCE_HELP)
    assert EVIDENCE_LABELS == {
        "official": "Official source",
        "lab_test": "Official lab result",
        "report": "News or NGO report",
        "study": "Peer-reviewed study",
        "provider": "Provider's own information",
    }
    for key in EVIDENCE_KEYS:
        assert EVIDENCE_HELP[key].strip().endswith("."), key


def test_finding_labels_match_their_links():
    findings = list(all_findings())
    for finding in findings:
        assert finding.evidence in EVIDENCE_KEYS, finding.text
        if finding.evidence in ("official", "lab_test"):
            # Only a government, Parliament or WHO page can carry an official label.
            assert source_kind(finding.url) == "official", (finding.evidence, finding.url)
        if "reported by" in finding.source.lower():
            assert finding.evidence == "report", finding.source
        if finding.evidence == "lab_test":
            # An official lab result is about the samples or batch that were tested.
            assert re.search(r"\b(samples?|batch)\b", finding.text), finding.text

    # Aggregate statistics stay "official": they are never an official lab result.
    aggregate = [*TOPICS_BY_SLUG["overview-india"].findings, WHY_IT_MATTERS, *TESTING_FACTS]
    aggregate += [f for f in findings if "%" in f.text]
    for finding in aggregate:
        assert finding.evidence != "lab_test", finding.text

    price = next(f for f in TESTING_FACTS if "pjtau.edu.in" in f.url)
    assert price.evidence == "provider" and "PJTAU" in price.text


def test_spice_findings_follow_the_official_sources():
    spices = {f.url: f for f in TOPICS_BY_SLUG["spices"].findings}
    hong_kong = spices["https://www.cfs.gov.hk/english/press/20240405_10919.html"]
    assert hong_kong.evidence == "lab_test" and "Hong Kong" in hong_kong.text and "samples" in hong_kong.text
    factories = next(f for f in spices.values() if "34 samples" in f.text)
    assert factories.evidence == "official" and "sansad.in" in factories.url
    everest = next(f for f in spices.values() if "E080668761" in f.text)
    assert everest.evidence == "report" and "referral food laboratory" in everest.text
    singapore = next(f for f in spices.values() if "Singapore" in f.text)
    assert singapore.evidence == "report" and "reported by" in singapore.source


def test_no_unsupported_or_removed_claims():
    text = all_text()
    for phrase in REMOVED_CLAIMS:
        assert phrase.lower() not in text.lower(), phrase
    for host in REMOVED_HOSTS:
        assert host not in text, host


def test_topics_have_unique_slugs_and_honest_home_testing_advice():
    assert len(TOPICS_BY_SLUG) == len(TOPICS)
    for topic in TOPICS:
        assert topic.findings and topic.what_you_can_do
        assert re.search(
            r"\b(cannot|can't|no reliable|not proof|not prove|only a laborator|not detect)", topic.home_testing, re.I
        ), topic.slug
        for text in (topic.summary, topic.concern, topic.home_testing, *(f.text for f in topic.findings)):
            assert not FORBIDDEN.search(text), (topic.slug, text)


def test_search_topics_matches_local_names():
    assert [t.slug for t in search_topics("haldi")] == ["turmeric"]
    assert "milk" in [t.slug for t in search_topics("paneer")]
    assert search_topics("") == []
