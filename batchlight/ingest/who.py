"""WHO Medical Product Alerts on substandard and falsified medical products.

WHO usually publishes the batch table in a separate annex file, so alerts
without an HTML table are stored at product level and link to the source notice.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..lots import LotRef, batch_rows_from_html
from ..text import lot_key, truncate
from .base import AlertRecord, SourceAdapter, get_with_retry, parse_date, register

LIST_URL = "https://www.who.int/teams/regulation-prequalification/incidents-and-SF/full-list-of-who-medical-product-alerts"
LINK_RE = re.compile(r"/news/item/(\d{2}-\d{2}-\d{4})-medical-product-alert[^\"'\s<>]*", re.IGNORECASE)
TITLE_PRODUCTS_RE = re.compile(r"\b(?:falsified|substandard)(?:\s*\((?:contaminated)\))?\s+(.+)$", re.IGNORECASE)


def alert_links(html: str) -> list[tuple[str, date | None]]:
    links: dict[str, date | None] = {}
    for match in LINK_RE.finditer(html):
        path = match.group(0)
        links.setdefault(urljoin("https://www.who.int", path), parse_date(match.group(1), "%d-%m-%Y"))
    return list(links.items())


def title_products(title: str) -> tuple[list[str], list[str]]:
    """'Falsified DARZALEX (daratumumab)' -> (['DARZALEX'], ['daratumumab'])."""
    match = TITLE_PRODUCTS_RE.search(title)
    if not match:
        return [], []
    names, ingredients = [], []
    for part in re.split(r",|\band\b", match.group(1)):
        part = part.strip()
        inner = re.findall(r"\(([^)]+)\)", part)
        name = re.sub(r"\([^)]*\)", "", part).strip(" -")
        name = re.sub(r"\b(injection|capsules?|tablets?|for injection|oral liquid medicines?)\b.*$", "", name, flags=re.I).strip()
        if name and name.lower() not in {"medicines", "products", "oral", "liquid"}:
            names.append(name)
        ingredients += [i.strip() for i in inner if i.strip().lower() != "contaminated"]
    return names, ingredients


BODY_PRODUCTS_RE = re.compile(r"batch(?:es)?\s+of\s+([^.]{3,200}?),?\s+manufactured\s+by\s+([^.]{3,200}?)\s*[.;]", re.IGNORECASE)


def _split_list(value: str) -> list[str]:
    return [p.strip(" ,") for p in re.split(r",|\band\b", value) if p.strip(" ,")]


def products_from_text(text: str) -> tuple[list[str], list[str]]:
    """'specific batches of COLDRIF, Respifresh TR and ReLife, manufactured by A, B and C.' -> (names, makers)."""
    match = BODY_PRODUCTS_RE.search(text)
    if not match:
        return [], []
    return _split_list(match.group(1)), _split_list(match.group(2))


def map_page(url: str, html: str, listed_date: date | None) -> AlertRecord:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    meta_title = soup.find("meta", attrs={"property": "og:title"})
    title = " ".join((heading.get_text(" ") if heading else (meta_title or {}).get("content", url)).split())
    body = soup.find(class_="sf-detail-body-wrapper") or soup
    text = " ".join(body.get_text(" ").split())
    lowered = title.lower()
    if "falsified" in lowered:
        alert_type, severity = "falsified", "critical"
    elif "substandard" in lowered or "contaminated" in lowered:
        alert_type, severity = "substandard", "critical"
    else:
        alert_type, severity = "safety_notice", "moderate"
    names, ingredients = title_products(title)
    rows = batch_rows_from_html(str(body))
    names += [r.product for r in rows if r.product]
    makers = [r.manufacturer for r in rows if r.manufacturer]
    if not names:
        body_names, body_makers = products_from_text(text)
        names += body_names
        makers += body_makers
    summary = re.search(r"Alert summary\s+(.+?)(?:\s+Advice to|\s+Product identification|$)", text, re.IGNORECASE)
    return AlertRecord(
        external_id=url.removeprefix("https://www.who.int"),
        title=title,
        severity=severity,
        alert_type=alert_type,
        source_url=url,
        published_at=listed_date,
        classification="WHO Medical Product Alert",
        product_description=truncate(text, 4000),
        product_names=names,
        active_ingredients=ingredients,
        manufacturer=", ".join(dict.fromkeys(makers)) or None,  # every maker, in WHO's order
        reason=truncate(summary.group(1) if summary else text, 1200),
        lots=[LotRef(lot=r.batch, key=lot_key(r.batch), expiry_text=r.expiry) for r in rows],
        lots_text=", ".join(r.batch for r in rows) or None,
        raw={"url": url},
    )


class WHOMedicalProductAlerts(SourceAdapter):
    id = "who-mpa"
    name = "WHO Medical Product Alerts (substandard and falsified)"
    regulator = "World Health Organization"
    country_code = None
    homepage_url = LIST_URL

    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        listing = get_with_retry(client, LIST_URL).text
        for url, listed in alert_links(listing):
            if listed and listed < since:
                continue
            response = get_with_retry(client, url, allow_404=True)
            if response is not None:
                yield map_page(url, response.text, listed)


register(WHOMedicalProductAlerts())
