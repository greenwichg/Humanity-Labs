"""Create, update, import and export holdings (stock lines or household medicines)."""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..matching import match_holdings, rescore_open_matches
from ..models import Holding
from ..text import lot_key, name_tokens

MAX_IMPORT_ROWS = 5000
MAX_IMPORT_BYTES = 2_000_000


def parse_flexible_date(value: str | date | None) -> date | None:
    """Accept 2027-04-30, 30/04/2027, 04/2027 and 2027-04 (month dates resolve to the last day of the month)."""
    if value is None or isinstance(value, date):
        return value
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    month = re.fullmatch(r"(\d{1,2})[/-](\d{4})", value) or re.fullmatch(r"(\d{4})-(\d{1,2})", value)
    if month:
        a, b = month.groups()
        year, mon = (int(b), int(a)) if len(b) == 4 else (int(a), int(b))
        if 1 <= mon <= 12:
            next_month = date(year + (mon == 12), mon % 12 + 1, 1)
            return date.fromordinal(next_month.toordinal() - 1)
    raise ValueError("Use a date like 2027-04-30, 30/04/2027 or 04/2027")


class HoldingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_name: str = Field(min_length=2, max_length=300)
    active_ingredient: str | None = Field(default=None, max_length=300)
    strength: str | None = Field(default=None, max_length=100)
    dosage_form: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=300)
    product_code: str | None = Field(default=None, max_length=50, pattern=r"^[A-Za-z0-9\-\s.]*$")
    lot: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    quantity: int | None = Field(default=None, ge=0, le=1_000_000_000)
    location: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("expiry_date", mode="before")
    @classmethod
    def flexible_date(cls, value):
        return parse_flexible_date(value)

    @field_validator("quantity", mode="before")
    @classmethod
    def integer_quantity(cls, value):
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            if not cleaned:
                return None
            if not re.fullmatch(r"\d+", cleaned):
                raise ValueError("Quantity must be a whole number")
            return int(cleaned)
        return value

    @field_validator("lot")
    @classmethod
    def lot_has_content(cls, value):
        if value is not None and not lot_key(value):
            raise ValueError("Batch number must contain letters or digits")
        return value


def apply_input(holding: Holding, data: HoldingInput) -> None:
    for name, value in data.model_dump().items():
        setattr(holding, name, value)
    holding.lot_key = lot_key(data.lot) or None
    holding.name_tokens = name_tokens(data.product_name, data.active_ingredient)
    holding.updated_at = datetime.now(UTC)


def create_holding(
    session: Session, org_id: uuid.UUID, data: HoldingInput, user_id: uuid.UUID | None
) -> tuple[Holding, list[uuid.UUID]]:
    holding = Holding(org_id=org_id, created_by=user_id)
    apply_input(holding, data)
    session.add(holding)
    session.flush()
    return holding, match_holdings(session, [holding])


def update_holding(session: Session, holding: Holding, data: HoldingInput) -> list[uuid.UUID]:
    """Save edits, drop open matches the edit invalidates (e.g. a corrected batch number), then re-match."""
    apply_input(holding, data)
    session.flush()
    rescore_open_matches(session, holding_ids=[holding.id])
    return match_holdings(session, [holding])


def archive_holding(session: Session, holding: Holding) -> None:
    """Remove a holding from the list. Open matches go with it; resolved matches stay as history."""
    holding.archived_at = datetime.now(UTC)
    session.flush()
    rescore_open_matches(session, holding_ids=[holding.id])


# --------------------------------------------------------------------------- CSV

CSV_COLUMNS = [
    "product_name",
    "active_ingredient",
    "strength",
    "dosage_form",
    "manufacturer",
    "product_code",
    "batch",
    "expiry_date",
    "quantity",
    "location",
    "notes",
]
_ALIASES = {
    "product": "product_name",
    "name": "product_name",
    "medicine": "product_name",
    "item": "product_name",
    "product name": "product_name",
    "ingredient": "active_ingredient",
    "active ingredient": "active_ingredient",
    "generic": "active_ingredient",
    "generic_name": "active_ingredient",
    "inn": "active_ingredient",
    "form": "dosage_form",
    "dosage form": "dosage_form",
    "maker": "manufacturer",
    "mah": "manufacturer",
    "ndc": "product_code",
    "gtin": "product_code",
    "din": "product_code",
    "code": "product_code",
    "product code": "product_code",
    "lot": "batch",
    "lot_number": "batch",
    "lot number": "batch",
    "batch_number": "batch",
    "batch number": "batch",
    "batch no": "batch",
    "expiry": "expiry_date",
    "exp": "expiry_date",
    "expiration": "expiry_date",
    "expiry date": "expiry_date",
    "qty": "quantity",
    "stock": "quantity",
    "units": "quantity",
    "note": "notes",
}

CSV_TEMPLATE = (
    ",".join(CSV_COLUMNS) + "\n"
    "Amoxicillin 500mg capsules,amoxicillin,500 mg,capsule,Example Pharma Ltd,,AMX2401,2027-03-31,120,Shelf B2,\n"
)


@dataclass
class ImportReport:
    created: int = 0
    duplicates: int = 0
    matches: int = 0
    errors: list[str] = field(default_factory=list)


def _canonical(header: str) -> str:
    key = header.strip().lower().replace("﻿", "")
    return _ALIASES.get(key, _ALIASES.get(key.replace("_", " "), key))


def parse_csv(raw: bytes) -> tuple[list[HoldingInput], list[str]]:
    """Validate a CSV file. Returns (rows, errors); errors reference spreadsheet line numbers."""
    if len(raw) > MAX_IMPORT_BYTES:
        return [], [f"The file is larger than {MAX_IMPORT_BYTES // 1_000_000} MB. Split it into smaller files."]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = [_canonical(h) for h in next(reader)]
    except StopIteration:
        return [], ["The file is empty."]
    if "product_name" not in header:
        return [], [
            "The file needs a product_name column (or 'product', 'name' or 'medicine'). Download the template to see the format."
        ]
    rows, errors = [], []
    for line_number, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(rows) + len(errors) >= MAX_IMPORT_ROWS:
            errors.append(f"Only {MAX_IMPORT_ROWS} rows can be imported at once.")
            break
        record = {col: values[i] for i, col in enumerate(header) if i < len(values) and col in CSV_COLUMNS}
        if "batch" in record:
            record["lot"] = record.pop("batch")
        try:
            rows.append(HoldingInput(**record))
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'row'}: {e['msg']}" for e in exc.errors())
            errors.append(f"Line {line_number}: {problems}")
        except ValueError as exc:
            errors.append(f"Line {line_number}: {exc}")
    if not rows and not errors:
        errors.append("The file has a header but no rows.")
    return rows, errors


def import_rows(session: Session, org_id: uuid.UUID, rows: list[HoldingInput], user_id: uuid.UUID | None) -> ImportReport:
    """Insert validated rows, skipping exact duplicates of active holdings, then match them."""
    report = ImportReport()
    existing = {
        (name.lower(), key or "", expiry)
        for name, key, expiry in session.execute(
            select(func.lower(Holding.product_name), Holding.lot_key, Holding.expiry_date).where(
                Holding.org_id == org_id, Holding.archived_at.is_(None)
            )
        )
    }
    created: list[Holding] = []
    for data in rows:
        signature = (data.product_name.lower(), lot_key(data.lot), data.expiry_date)
        if signature in existing:
            report.duplicates += 1
            continue
        existing.add(signature)
        holding = Holding(org_id=org_id, created_by=user_id)
        apply_input(holding, data)
        session.add(holding)
        created.append(holding)
    session.flush()
    report.created = len(created)
    report.matches = len(match_holdings(session, created))
    return report


def _cell(value) -> str:
    """Neutralise spreadsheet formulas: a cell starting with = + - @ would be evaluated by Excel or Sheets."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(holdings: list[Holding]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for h in holdings:
        writer.writerow(
            [
                _cell(v)
                for v in [
                    h.product_name,
                    h.active_ingredient or "",
                    h.strength or "",
                    h.dosage_form or "",
                    h.manufacturer or "",
                    h.product_code or "",
                    h.lot or "",
                    h.expiry_date.isoformat() if h.expiry_date else "",
                    "" if h.quantity is None else h.quantity,
                    h.location or "",
                    h.notes or "",
                ]
            ]
        )
    return out.getvalue()
