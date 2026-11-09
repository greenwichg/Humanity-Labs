"""UK MHRA drug and device alerts via the GOV.UK search and content APIs.

Content is published under the Open Government Licence v3.0.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..lots import LotRef, batch_rows_from_html, extract_lots
from ..text import extract_product_codes, lot_key, truncate
from .base import AlertRecord, SourceAdapter, get_with_retry, parse_date, register

SEARCH_URL = "https://www.gov.uk/api/search.json"
CONTENT_URL = "https://www.gov.uk/api/content"
SKIP_TITLE_RE = re.compile(r"^\s*(field safety notices?\b|mhra safety roundup|drug safety update)", re.IGNORECASE)
CLASS_RE = re.compile(r"\bclass\s*([1-4])\b", re.IGNORECASE)
CODE_SUFFIX_RE = re.compile(r",?\s*\b[A-Z]{2,4}\s?\(\d{2}\)\s?[A-Z]/\d+\s*$")
CLASS_SEVERITY = {"1": "critical", "2": "high", "3": "moderate", "4": "low"}


def classify(title: str, alert_type: str | None) -> tuple[str, str]:
    """(severity, alert_type) from an MHRA title such as 'Class 2 Medicines Recall: ...'."""
    lowered = title.lower()
    match = CLASS_RE.search(title)
    severity = CLASS_SEVERITY.get(match.group(1), "moderate") if match else None
    if "recall" in lowered:
        kind = "recall"
    elif "defect" in lowered or "caution in use" in lowered:
        kind = "quality_defect"
    elif "falsified" in lowered or "counterfeit" in lowered:
        kind = "falsified"
    else:
        kind = "safety_notice"
    if severity is None:
        severity = (
            "high" if "national patient safety alert" in lowered or (alert_type or "").startswith("national") else "moderate"
        )
    return severity, kind


def split_title(title: str) -> tuple[str | None, str]:
    """'Class 3 Medicines Recall: Aspire Pharma Limited, Tropicamide 1% Eye Drops, ..., EL(26)A/47' ->
    ('Aspire Pharma Limited', 'Tropicamide 1% Eye Drops, ...')."""
    head, sep, rest = title.partition(":")
    if not sep or not CLASS_RE.search(head):
        return None, title.strip()
    rest = CODE_SUFFIX_RE.sub("", rest.strip())
    company, _, product = rest.partition(",")
    return company.strip() or None, product.strip(" ,") or rest.strip()


def labelled(text: str, label: str) -> str | None:
    match = re.search(
        rf"{label}\s*:?\s*(.+?)(?=\s+(?:Active ingredient|SNOMED|GTIN|PL\s*:|Marketing Authorisation|"
        rf"Affected Lot|Batch No|Medicine Details|Background|Advice)\b|$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip(" .") if match else None


def map_content(link: str, content: dict) -> AlertRecord:
    details = content.get("details", {})
    metadata = details.get("metadata", {})
    body_html = details.get("body") or ""
    text = " ".join(BeautifulSoup(body_html, "html.parser").get_text(" ").split())
    title = content.get("title", "").strip()
    severity, alert_type = classify(title, metadata.get("alert_type"))
    company, product = split_title(title)
    holder = labelled(text, "Marketing Authorisation Holder") or company
    ingredient = labelled(text, "Active ingredient")
    rows = batch_rows_from_html(body_html)
    lots = [LotRef(lot=r.batch, key=lot_key(r.batch), expiry_text=r.expiry) for r in rows]
    all_lots = False
    if not lots:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(r"\b(batch|lot)\b", s, re.IGNORECASE)]
        extracted = extract_lots(*sentences)
        lots, all_lots = extracted.lots, extracted.all_lots
    names = [product] + ([r.product for r in rows if r.product] if rows else [])
    return AlertRecord(
        external_id=link,
        title=title,
        severity=severity,
        alert_type=alert_type,
        source_url=f"https://www.gov.uk{link}",
        published_at=parse_date(metadata.get("issued_date")) or parse_date(content.get("first_published_at")),
        classification=(CLASS_RE.search(title).group(0).title() if CLASS_RE.search(title) else metadata.get("alert_type")),
        product_description=truncate(text, 4000),
        product_names=names,
        active_ingredients=[ingredient] if ingredient else [],
        manufacturer=holder,
        product_codes=extract_product_codes(text),
        reason=truncate(content.get("description"), 1000),
        lots=lots,
        all_lots=all_lots,
        lots_text=", ".join(lot.lot for lot in lots) or None,
        raw={
            "link": link,
            "alert_type": metadata.get("alert_type"),
            "medical_specialism": metadata.get("medical_specialism"),
            "issued_date": metadata.get("issued_date"),
        },
    )


class MHRAAlerts(SourceAdapter):
    id = "uk-mhra"
    name = "UK MHRA drug and device alerts"
    regulator = "UK Medicines and Healthcare products Regulatory Agency"
    country_code = "GB"
    homepage_url = "https://www.gov.uk/drug-device-alerts"

    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        limit = get_settings().mhra_max_items
        start, seen = 0, 0
        while seen < limit:
            params = {
                "filter_format": "medical_safety_alert",
                "count": 100,
                "start": start,
                "order": "-public_timestamp",
                "fields": "title,link,public_timestamp,description,alert_type",
            }
            results = get_with_retry(client, SEARCH_URL, params=params).json().get("results", [])
            if not results:
                return
            for item in results:
                seen += 1
                published = parse_date(item.get("public_timestamp"))
                if published and published < since:
                    return
                if SKIP_TITLE_RE.match(item.get("title", "")) or not item.get("link"):
                    continue
                response = get_with_retry(client, f"{CONTENT_URL}{item['link']}", allow_404=True)
                if response is not None:
                    yield map_content(item["link"], response.json())
                if seen >= limit:
                    return
            start += 100


register(MHRAAlerts())
