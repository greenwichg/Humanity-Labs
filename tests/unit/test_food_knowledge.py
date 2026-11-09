import re

from batchlight.food.knowledge import EVIDENCE_LABELS, TESTING_FACTS, TOPICS, TOPICS_BY_SLUG, WHY_IT_MATTERS, search_topics

FORBIDDEN = re.compile(
    r"\b(detects? adulteration|guarantee[sd]?|100% safe|is adulterated|home test (can|will) (show|prove))\b", re.I
)


def all_findings():
    for topic in TOPICS:
        yield from topic.findings
    yield WHY_IT_MATTERS
    yield from TESTING_FACTS


def test_every_finding_is_sourced_and_graded():
    for finding in all_findings():
        assert finding.url.startswith("https://"), finding.text
        assert finding.evidence in EVIDENCE_LABELS, finding.text
        assert finding.source and finding.year, finding.text
        assert len(finding.text) > 40, finding.text


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
