"""Extract batch (lot) numbers from regulator free text and HTML tables.

Regulators describe affected batches in inconsistent prose, for example::

    Lot #: 83449, Exp Date: 10/2026; 82682, Exp Date: 11/2026
    Material Number: M001195290; UDI-DI: 08714729839408; Batch Numbers: 38362172, 38362173
    All lots within expiry.

The parser removes expiry dates and other identifiers, splits what remains
into candidate tokens and keeps tokens that look like batch codes. Expiry text
found in the same clause is attached to the batches in that clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .text import lot_key

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    r"\b\d{4}-\d{1,2}-\d{1,2}\b",  # ISO
    r"\b(?:0?[1-9]|[12]\d|3[01])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:\d{4}|\d{2})\b",  # 10/31/2025, 31.10.25
    r"\b(?:0?[1-9]|1[0-2])/(?:\d{4}|\d{2})\b",  # 06/2024
    r"\b(?:19|20)\d{2}[/-](?:0?[1-9]|1[0-2])\b",  # 2026/07
    rf"\b{_MONTH}\s*\d{{1,2}}(?:\s*-\s*\d{{1,2}})?,?\s*\d{{4}}\b",  # Oct 31, 2026 / December 24-29, 2022
    rf"\b\d{{1,2}}[\s\-/.]+{_MONTH}[\s\-/.,]*\d{{2,4}}\b",  # 31 Oct 2026 / 31-Oct-26
    rf"\b{_MONTH}[\s\-/,]*\d{{2,4}}\b",  # July 2027 / Jun-26 / April , 2025
]
_DATE_RE = re.compile("|".join(f"(?:{p})" for p in _DATE_PATTERNS), re.IGNORECASE)
_EXPIRY_RE = re.compile(
    r"\b(?:expiry|expiration|expires|exp|use\s+by|best\s+before|beyond\s+use\s+date|bud)\b[\W_]{0,4}(?:date\b[\W_]{0,4})?"
    rf"(?P<date>{_DATE_RE.pattern})",
    re.IGNORECASE,
)
_ALL_LOTS_RE = re.compile(
    r"\ball\s+(?:lots?|batch(?:es)?|lot\s+numbers|batch\s+numbers|units|product(?:s)?|codes?|serial\s+numbers)\b"
    r"|\b(?:lots?|batches)\s*:\s*all\b",
    re.IGNORECASE,
)
_OTHER_IDS_RE = re.compile(
    r"\b(?:NDC|UPC|GTIN|EAN|UDI(?:-DI)?|SKU|REF|Model(?:\s+(?:No\.?|Number))?|Catalog(?:ue)?(?:\s*(?:#|No\.?|Number))?|"
    r"Item(?:\s*(?:#|No\.?|Number))?|Material\s+(?:No\.?|Number)|Part\s+(?:No\.?|Number)|Product\s+Code|"
    r"Serial\s+(?:No\.?|Numbers?)|PL|DIN|NPN)\s*[:#.]?\s*[\w\-\.]+(?:\s+\d[\w\-\.]*)?",
    re.IGNORECASE,
)
_ENUM_MARKER_RE = re.compile(r"(?:^|[\s;:,])\(?[a-z]\)\s*", re.IGNORECASE)
_LABEL_WORDS = frozenset(
    {
        "lot",
        "lots",
        "batch",
        "batches",
        "number",
        "numbers",
        "no",
        "nos",
        "code",
        "codes",
        "and",
        "or",
        "lot#",
        "batch#",
        "including",
        "only",
        "following",
        "affected",
        "date",
    }
)
_TOKEN_SPLIT_RE = re.compile(r"[,\s&;]+|\band\b", re.IGNORECASE)
_VALID_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/\.@]*$")
_QUANTITY_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:count|ct|tablets?|capsules?|caplets?|ml|mg|mcg|g|units?|vials?|packs?|bottles?|doses?|"
    r"syringes?|bags?|tubes?|cartons?|boxes|x)\b",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(r"^\d+(?:\.\d+)?(?:mg|mcg|ml|g|kg|l|oz|iu|%|units?|ct|count|pk|x)$", re.IGNORECASE)
_DAY_RANGE_RE = re.compile(r"^\d{1,2}-\d{1,2}$")
_YEAR_RE = re.compile(r"^(?:19[5-9]\d|20\d\d)$")


@dataclass
class LotRef:
    lot: str
    key: str
    expiry_text: str | None = None


@dataclass
class LotExtraction:
    lots: list[LotRef] = field(default_factory=list)
    all_lots: bool = False

    @property
    def keys(self) -> list[str]:
        return [lot.key for lot in self.lots]


def _clean_token(token: str) -> str:
    return token.strip(" .:;,()[]{}#*'\"")


def _is_lot_candidate(token: str, clause_has_expiry: bool) -> bool:
    if not token or token.lower() in _LABEL_WORDS:
        return False
    if not any(ch.isdigit() for ch in token):
        return False
    if not 3 <= len(token) <= 30:
        return False
    if not _VALID_TOKEN_RE.match(token):
        return False
    if _UNIT_RE.match(token) or _DAY_RANGE_RE.match(token):
        return False
    if _DATE_RE.fullmatch(token):
        return False
    if clause_has_expiry and _YEAR_RE.match(token):
        return False
    return True


def extract_lots(*texts: str | None) -> LotExtraction:
    """Extract batch numbers from one or more free-text fields."""
    result = LotExtraction()
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        if _ALL_LOTS_RE.search(text):
            result.all_lots = True
        for clause in re.split(r"[;\n]+|\s\(?[b-h]\)\s", text):
            expiry_match = _EXPIRY_RE.search(clause)
            expiry_text = expiry_match.group("date").strip() if expiry_match else None
            cleaned = _EXPIRY_RE.sub(" ", clause)
            cleaned = _OTHER_IDS_RE.sub(" ", cleaned)
            cleaned = _QUANTITY_RE.sub(" ", cleaned)
            cleaned = _DATE_RE.sub(" ", cleaned)
            cleaned = _ENUM_MARKER_RE.sub(" ", cleaned)
            cleaned = re.sub(r"(?i)\b(?:lot|batch)(?:es|s)?\s*(?:numbers?|nos?\.?)?\s*[#:.]*", " ", cleaned)
            for raw_token in _TOKEN_SPLIT_RE.split(cleaned):
                token = _clean_token(raw_token or "")
                if not _is_lot_candidate(token, expiry_match is not None):
                    continue
                parts = [token]
                if "/" in token:
                    parts += [p for p in token.split("/") if _is_lot_candidate(_clean_token(p), True)]
                for part in parts:
                    key = lot_key(part)
                    if len(key) < 3 or key in seen:
                        continue
                    seen.add(key)
                    result.lots.append(LotRef(lot=part, key=key, expiry_text=expiry_text))
    return result


# --------------------------------------------------------------------------- tables

_BATCH_HEADER_RE = re.compile(r"\b(batch|lot)\b", re.IGNORECASE)
_EXPIRY_HEADER_RE = re.compile(r"\bexp", re.IGNORECASE)
_PRODUCT_HEADER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bproduct\s+name\b",
        r"\bmedicine\s+name\b",
        r"\bproduct\b",
        r"\bmedicine\b",
        r"\bname\b",
        r"\bdevice\b",
        r"\bdescription\b",
    )
]
_MANUFACTURER_HEADER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmanufacturer\b",
        r"\bmarketing authori[sz]ation holder\b",
        r"\bmah\b",
        r"\bcompany\b",
        r"\bfirm\b",
        r"\bbrand\b",
    )
]


@dataclass
class TableRow:
    batch: str
    expiry: str | None = None
    product: str | None = None
    manufacturer: str | None = None
    cells: dict[str, str] = field(default_factory=dict)


def html_tables(html: str) -> list[list[list[str]]]:
    """Return every table in the HTML as rows of whitespace-normalised cell text."""
    soup = BeautifulSoup(html or "", "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [" ".join(cell.get_text(" ").split()) for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _find_column(header: list[str], patterns: list[re.Pattern[str]], exclude: set[int] = frozenset()) -> int | None:
    for pattern in patterns:
        for index, cell in enumerate(header):
            if index not in exclude and pattern.search(cell):
                return index
    return None


def batch_rows_from_html(html: str) -> list[TableRow]:
    """Rows of every table whose header names a batch or lot column.

    A batch cell can list several batches ("A123, A124"); each becomes a row.
    Tables laid out vertically (a "Batch number" label in the first column)
    are supported too. ``cells`` keeps every value keyed by lowercase header.
    """
    rows: list[TableRow] = []
    for table in html_tables(html):
        header = table[0]
        batch_col = _find_column(header, [_BATCH_HEADER_RE])
        if batch_col is not None and len(table) > 1:
            expiry_col = _find_column(header, [_EXPIRY_HEADER_RE], {batch_col})
            product_col = _find_column(header, _PRODUCT_HEADER_PATTERNS, {batch_col})
            maker_col = _find_column(header, _MANUFACTURER_HEADER_PATTERNS, {batch_col, product_col or -1})
            for row in table[1:]:
                if batch_col >= len(row) or not row[batch_col].strip():
                    continue

                def cell(col: int | None, row: list[str] = row) -> str | None:
                    return row[col].strip() if col is not None and col < len(row) and row[col].strip() else None

                cells = {header[i].strip().lower(): row[i] for i in range(min(len(header), len(row))) if row[i].strip()}
                for lot in extract_lots(row[batch_col]).lots or _bare_cell_lots(row[batch_col]):
                    rows.append(
                        TableRow(
                            batch=lot.lot,
                            expiry=cell(expiry_col),
                            product=cell(product_col),
                            manufacturer=cell(maker_col),
                            cells=cells,
                        )
                    )
            continue
        # Vertical layout: first column holds labels.
        labels = {r[0].lower(): r[1] for r in table if len(r) >= 2}
        batch_label = next((k for k in labels if _BATCH_HEADER_RE.search(k)), None)
        if batch_label:

            def first(
                patterns: list[re.Pattern[str]], labels: dict[str, str] = labels, batch_label: str = batch_label
            ) -> str | None:
                for pattern in patterns:
                    for key, value in labels.items():
                        if key != batch_label and pattern.search(key):
                            return value
                return None

            for lot in extract_lots(labels[batch_label]).lots or _bare_cell_lots(labels[batch_label]):
                rows.append(
                    TableRow(
                        batch=lot.lot,
                        expiry=first([_EXPIRY_HEADER_RE]),
                        product=first(_PRODUCT_HEADER_PATTERNS),
                        manufacturer=first(_MANUFACTURER_HEADER_PATTERNS),
                        cells=dict(labels),
                    )
                )
    return rows


def _bare_cell_lots(value: str) -> list[LotRef]:
    """Batch cells sometimes hold a code with no digits-free noise, e.g. 'E941A'."""
    token = _clean_token(value)
    key = lot_key(token)
    if 3 <= len(key) <= 30 and any(ch.isdigit() for ch in key):
        return [LotRef(lot=token, key=key)]
    return []
