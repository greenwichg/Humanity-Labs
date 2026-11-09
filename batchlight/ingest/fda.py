"""US FDA drug and device recalls from the openFDA enforcement API.

openFDA data is in the public domain. See https://open.fda.gov/apis/.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from ..config import get_settings
from ..lots import extract_lots
from ..text import extract_product_codes, truncate
from .base import AlertRecord, SourceAdapter, get_with_retry, parse_date, register

SEVERITY = {"Class I": "critical", "Class II": "high", "Class III": "moderate"}
PAGE_SIZE = 1000
MAX_SKIP = 25000


def short_product(description: str) -> str:
    """Leading product name from an FDA product description ("Cinacalcet Tablets, 30 mg, ..." -> "Cinacalcet Tablets")."""
    head = re.split(r",|;|\(|\bNDC\b|\bRx only\b", description or "", maxsplit=1)[0]
    return truncate(head.strip(" .-"), 160)


def parenthetical_ingredients(description: str) -> list[str]:
    """Ingredient text in parentheses near the start: "(sodium fluoride 0.243%)" -> ["sodium fluoride"]."""
    out = []
    for inner in re.findall(r"\(([^()]{3,80})\)", (description or "")[:300]):
        name = re.sub(r"[\d.,]+\s*(%|mg|mcg|ml|g|iu)\b.*", "", inner, flags=re.IGNORECASE).strip(" ,.")
        if name and not re.search(r"\b(NDC|count|vial|mL|Item|UPC)\b", name, re.IGNORECASE) and len(name) >= 3:
            out.append(name)
    return out


def fallback_id(item: dict) -> str:
    """openFDA occasionally publishes a record without a recall number; derive a stable id."""
    basis = "|".join(str(item.get(k, "")) for k in ("event_id", "product_description", "code_info"))
    return f"event-{item.get('event_id', 'unknown')}-{hashlib.sha256(basis.encode()).hexdigest()[:12]}"


def map_record(kind: str, item: dict) -> AlertRecord:
    openfda = item.get("openfda") or {}
    description = item.get("product_description") or ""
    reason = (item.get("reason_for_recall") or "").strip()
    product = short_product(description)
    lots = extract_lots(item.get("code_info"), item.get("more_code_info"))
    names = [
        product,
        *(openfda.get("brand_name") or []),
        *(openfda.get("generic_name") or []),
        *parenthetical_ingredients(description),
    ]
    ingredients = list(openfda.get("generic_name") or []) + list(openfda.get("substance_name") or [])
    recall_number = item.get("recall_number") or fallback_id(item)
    return AlertRecord(
        external_id=recall_number,
        title=truncate(f"{product}: {reason}" if reason else product, 300),
        severity=SEVERITY.get(item.get("classification", ""), "moderate"),
        alert_type="recall",
        source_url=f"https://api.fda.gov/{kind}/enforcement.json?search=recall_number:%22{quote(recall_number)}%22",
        published_at=parse_date(item.get("report_date")),
        initiated_at=parse_date(item.get("recall_initiation_date")),
        classification=item.get("classification"),
        status=item.get("status"),
        product_description=description,
        product_names=names,
        active_ingredients=ingredients,
        manufacturer=item.get("recalling_firm"),
        product_codes=list(openfda.get("product_ndc") or []) + extract_product_codes(description, item.get("code_info")),
        reason=reason,
        distribution=item.get("distribution_pattern"),
        lots=lots.lots,
        all_lots=lots.all_lots,
        lots_text=" ".join(filter(None, [item.get("code_info"), item.get("more_code_info")])) or None,
        raw={
            k: item.get(k)
            for k in (
                "recall_number",
                "event_id",
                "recalling_firm",
                "city",
                "state",
                "country",
                "voluntary_mandated",
                "initial_firm_notification",
                "product_quantity",
                "code_info",
                "openfda",
            )
        },
    )


class OpenFDAEnforcement(SourceAdapter):
    def __init__(self, kind: str) -> None:
        assert kind in ("drug", "device")
        self.kind = kind
        self.id = f"us-fda-{kind}"
        self.name = f"US FDA {'drug' if kind == 'drug' else 'medical device'} recalls"
        self.regulator = "US Food and Drug Administration"
        self.country_code = "US"
        self.homepage_url = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"

    def _windows(self, since: date) -> Iterator[tuple[date, date]]:
        """Yearly windows keep each query under openFDA's skip limit."""
        start, today = since, date.today()
        while start <= today:
            end = min(date(start.year, 12, 31), today)
            yield start, end
            start = end + timedelta(days=1)

    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        api_key = get_settings().openfda_api_key
        url = f"https://api.fda.gov/{self.kind}/enforcement.json"
        for start, end in self._windows(since):
            skip = 0
            while skip <= MAX_SKIP:
                params = {
                    "search": f"report_date:[{start:%Y%m%d} TO {end:%Y%m%d}]",
                    "limit": PAGE_SIZE,
                    "skip": skip,
                    "sort": "report_date:asc",
                }
                if api_key:
                    params["api_key"] = api_key
                response = get_with_retry(client, url, params=params, allow_404=True)
                if response is None:  # openFDA answers 404 when a window has no results
                    break
                payload = response.json()
                results = payload.get("results", [])
                for item in results:
                    yield map_record(self.kind, item)
                total = payload.get("meta", {}).get("results", {}).get("total", 0)
                skip += PAGE_SIZE
                if skip >= total or not results:
                    break


register(OpenFDAEnforcement("drug"))
register(OpenFDAEnforcement("device"))
