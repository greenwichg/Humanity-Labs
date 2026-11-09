"""Canadian Food Inspection Agency (CFIA) food recalls.

CFIA recalls are published in the same Government of Canada "Recalls and
Safety Alerts" open data file as Health Canada's (Open Government Licence -
Canada). Alert pages list each affected product with brand, size, UPC
barcode and printed codes, which lets a consumer check the pack in hand.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..config import get_settings
from ..food.verify import UPC_FIRST, gtins_in_text
from ..lots import LotRef, extract_lots, html_tables
from .base import AlertRecord, register
from .healthcanada import HealthCanadaRecalls, clean, company_from_page, parse_date

CLASS_SEVERITY = {"class 1": "critical", "class 2": "high", "class 3": "moderate"}
ALL_CODES_RE = re.compile(r"\ball\s+(?:codes|units|lots|best before dates?|products?)\b", re.IGNORECASE)
NO_BRAND = re.compile(r"^(none|n/?a|not applicable|no brand)\b", re.IGNORECASE)


SENTENCE_STARTS = (
    "Do",
    "Check",
    "Recalled",
    "Learn",
    "Sign",
    "View",
    "Report",
    "Consumers",
    "If",
    "Contact",
    "Return",
    "Throw",
    "Retailers",
    "Restaurants",
    "Distributors",
    "Seek",
    "Food",
    "Hospitals",
    "Anyone",
    "Keep",
)
_SENTENCE_RE = re.compile(r"(?<=[a-z\)])(?=(?:" + "|".join(SENTENCE_STARTS) + r")\b)")


def restore_sentences(text: str) -> str:
    """The open data joins list items without spaces ("recalled productsDo not consume").

    Breaks are restored only before words that start CFIA's standard advice, so
    names such as "McCain" or "PepsiCo" are left alone.
    """
    return _SENTENCE_RE.sub(". ", text or "")


def map_food_record(item: dict) -> AlertRecord:
    recall_class = (item.get("Recall class") or "").strip().lower()
    product = clean(item.get("Product"))
    return AlertRecord(
        external_id=str(item["NID"]),
        title=clean(item.get("Title")) or product,
        severity=CLASS_SEVERITY.get(recall_class.split(" -")[0], "moderate"),
        alert_type="recall",
        source_url=item.get("URL") or "https://recalls-rappels.canada.ca/en",
        published_at=parse_date(item.get("Last updated")),
        classification=(item.get("Recall class") or None),
        status="Archived" if str(item.get("Archived")) == "1" else "Active",
        product_names=[product] if product else [],
        reason=clean(item.get("Issue")) or None,
        product_description=restore_sentences(clean(item.get("What you should do"))) or None,
        partial=True,
        raw={k: item.get(k) for k in ("NID", "Organization", "Category", "Recall class", "Last updated", "Archived")},
    )


def enrich_food_page(record: AlertRecord, html: str) -> AlertRecord:
    """Brands, UPC barcodes and printed codes from the affected-products table."""
    record.partial = False
    record.raw["detail_fetched"] = True
    lots: list[LotRef] = []
    for table in html_tables(html):
        header = [cell.strip().lower() for cell in table[0]]
        if "upc" not in header and "codes" not in header:
            continue
        col = {name: header.index(name) for name in ("brand", "product", "upc", "codes") if name in header}
        for row in table[1:]:

            def cell(name: str, row: list[str] = row, col: dict = col) -> str:
                index = col.get(name)
                return row[index].strip() if index is not None and index < len(row) else ""

            brand, product = cell("brand"), cell("product")
            if brand and not NO_BRAND.match(brand):
                record.product_names.append(f"{brand} {product}".strip())
                record.manufacturer = record.manufacturer or brand
            elif product:
                record.product_names.append(product)
            record.product_codes += gtins_in_text(cell("upc"), UPC_FIRST)
            codes = cell("codes")
            if ALL_CODES_RE.search(codes):
                record.all_lots = True
            lots += extract_lots(codes).lots
    seen: set[str] = set()
    record.lots = [lot for lot in lots if not (lot.key in seen or seen.add(lot.key))]
    record.lots_text = ", ".join(lot.lot for lot in record.lots) or None
    soup = BeautifulSoup(html, "html.parser")
    company = company_from_page(soup)
    if company:
        record.manufacturer = company
    return record


class CFIAFoodRecalls(HealthCanadaRecalls):
    id = "ca-cfia-food"
    name = "Canadian Food Inspection Agency food recalls"
    regulator = "Canadian Food Inspection Agency"
    country_code = "CA"
    homepage_url = "https://recalls-rappels.canada.ca/en"
    category = "food"

    def is_relevant(self, item: dict) -> bool:
        return item.get("Organization") == "CFIA"

    def map(self, item: dict) -> AlertRecord:
        return map_food_record(item)

    def enrich(self, record: AlertRecord, html: str) -> AlertRecord:
        return enrich_food_page(record, html)

    @property
    def detail_limit(self) -> int:
        return get_settings().cfia_detail_limit


register(CFIAFoodRecalls())
