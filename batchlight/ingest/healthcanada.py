"""Health Canada recalls and safety alerts for health products.

Uses the Recalls and Safety Alerts open data file (Open Government Licence - Canada)
and, for recent alerts, the alert page's affected-products table for lot numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..lots import LotRef, batch_rows_from_html
from ..text import lot_key, truncate
from .base import AlertRecord, SourceAdapter, get_with_retry, parse_date, register

OPEN_DATA_URL = "https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json"
HEALTH_CATEGORIES = re.compile(r"\b(drugs?|biologic|vaccine|natural health|medical devices?|health products?|radiopharm)", re.I)
EXCLUDED_CATEGORIES = re.compile(r"veterinary|cannabis", re.I)
HEALTH_ORGS = {
    "Drugs and health products",
    "Marketed health products",
    "Medical devices",
    "Controlled substances and cannabis",
    "HC",
}
TYPE_SEVERITY = {"type i": "critical", "type ii": "high", "type iii": "moderate"}
ZERO_WIDTH = re.compile("[​‌‍﻿]")


def clean(value: str | None) -> str:
    return " ".join(ZERO_WIDTH.sub("", BeautifulSoup(value or "", "html.parser").get_text(" ")).split())


def is_health_record(item: dict) -> bool:
    category = item.get("Category") or ""
    if EXCLUDED_CATEGORIES.search(category):
        return False
    return bool(HEALTH_CATEGORIES.search(category)) or item.get("Organization") in HEALTH_ORGS and bool(category)


def classify(item: dict) -> tuple[str, str]:
    recall_class = (item.get("Recall class") or "").strip().lower()
    issue = f"{item.get('Issue', '')} {item.get('Title', '')}".lower()
    if "counterfeit" in issue or "falsified" in issue:
        return TYPE_SEVERITY.get(recall_class, "critical"), "falsified"
    if recall_class in TYPE_SEVERITY:
        return TYPE_SEVERITY[recall_class], "recall"
    if "unauthorized" in issue or "unauthorised" in issue:
        return "high", "safety_notice"
    if "recall" in issue:
        return "moderate", "recall"
    return "moderate", "safety_notice"


def map_record(item: dict) -> AlertRecord:
    severity, alert_type = classify(item)
    product = clean(item.get("Product"))
    return AlertRecord(
        external_id=str(item["NID"]),
        title=clean(item.get("Title")) or product,
        severity=severity,
        alert_type=alert_type,
        source_url=item.get("URL") or "https://recalls-rappels.canada.ca/en",
        published_at=parse_date(item.get("Last updated")),
        classification=(item.get("Recall class") or None),
        status="Archived" if str(item.get("Archived")) == "1" else "Active",
        product_names=[product] if product else [],
        reason=clean(item.get("Issue")) or None,
        product_description=truncate(clean(item.get("What you should do")), 2000) or None,
        raw={k: item.get(k) for k in ("NID", "Organization", "Category", "Recall class", "Last updated", "Archived")},
    )


def enrich_from_page(record: AlertRecord, html: str) -> AlertRecord:
    """Add lot numbers, products, DINs and the recalling company from the alert page."""
    rows = batch_rows_from_html(html)
    if rows:
        record.lots = [LotRef(lot=r.batch, key=lot_key(r.batch), expiry_text=r.expiry) for r in rows]
        record.lots_text = ", ".join(r.batch for r in rows)
        record.product_names += [r.product for r in rows if r.product]
        companies = [r.manufacturer for r in rows if r.manufacturer]
        if companies:
            record.manufacturer = companies[0]
        for r in rows:
            authorisation = r.cells.get("market authorization") or r.cells.get("din") or ""
            record.product_codes += re.findall(r"\b\d{8}\b", authorisation)
    text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())
    published = re.search(r"Original published date:\s*(\d{4}-\d{2}-\d{2})", text)
    if published:
        record.initiated_at = parse_date(published.group(1))
    if not record.manufacturer:
        company = re.search(r"\bCompanies\s+(.+?)(?:\s+\d|\s+Published by)", text)
        if company:
            record.manufacturer = company.group(1).strip()
    if re.search(r"\ball lots\b", text, re.IGNORECASE) and not record.lots:
        record.all_lots = True
    return record


class HealthCanadaRecalls(SourceAdapter):
    id = "ca-health-canada"
    name = "Health Canada health product recalls and alerts"
    regulator = "Health Canada"
    country_code = "CA"
    homepage_url = "https://recalls-rappels.canada.ca/en"

    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        settings = get_settings()
        items = get_with_retry(client, OPEN_DATA_URL).json()
        records = [
            item for item in items if is_health_record(item) and (parse_date(item.get("Last updated")) or date.min) >= since
        ]
        records.sort(key=lambda item: item.get("Last updated") or "", reverse=True)
        detail_cutoff = date.today() - timedelta(days=settings.health_canada_detail_days)
        details_fetched = 0
        for item in records:
            record = map_record(item)
            wants_detail = (record.published_at or date.min) >= detail_cutoff and item.get("URL", "").startswith(
                "https://recalls-rappels.canada.ca/"
            )
            if wants_detail and details_fetched < settings.health_canada_detail_limit:
                details_fetched += 1
                response = get_with_retry(client, item["URL"], allow_404=True)
                if response is not None:
                    record = enrich_from_page(record, response.text)
            yield record


register(HealthCanadaRecalls())
