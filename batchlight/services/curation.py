"""Alerts added by hand or by CSV, for regulators that publish only PDFs or images.

Each one declares what its link is (``evidence``): the regulator's own notice ("official"), an official
laboratory result ("lab_test") or a news or NGO report ("report"). The two official kinds must link to a
government, Parliament or WHO website.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator
from sqlalchemy.orm import Session

from ..ingest.base import AlertRecord, ensure_sources, upsert_alert
from ..lots import LotRef, extract_lots
from ..matching import match_alerts, rescore_open_matches
from ..models import ALERT_TYPES, CATEGORIES, EVIDENCES, SEVERITIES, Alert, Source
from ..text import lot_key, source_kind
from .notifications import notify_new_matches


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in re.split(r"[;\n]|,(?![^()]*\))", value or "") if part.strip()]


class CuratedAlertInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = "medicine"
    regulator: str = Field(min_length=2, max_length=100)
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    title: str = Field(min_length=5, max_length=500)
    alert_type: str
    severity: str
    product_names: str = Field(min_length=2, max_length=2000)
    active_ingredients: str | None = Field(default=None, max_length=1000)
    manufacturer: str | None = Field(default=None, max_length=300)
    product_codes: str | None = Field(default=None, max_length=1000)
    batches: str | None = Field(default=None, max_length=20000)
    all_lots: bool = False
    reason: str | None = Field(default=None, max_length=4000)
    published_at: date
    source_url: str = Field(max_length=1000, pattern=r"^https://[^\s]+$")
    external_id: str | None = Field(default=None, max_length=200)
    # Declared last so its validator can see source_url and reason.
    evidence: str

    @field_validator("*", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("category", mode="before")
    @classmethod
    def valid_category(cls, value):
        value = (value or "medicine").strip().lower() if isinstance(value, str) else "medicine"
        if value not in CATEGORIES:
            raise ValueError(f"must be one of {', '.join(CATEGORIES)}")
        return value

    @field_validator("alert_type")
    @classmethod
    def valid_type(cls, value):
        if value not in ALERT_TYPES:
            raise ValueError(f"must be one of {', '.join(ALERT_TYPES)}")
        return value

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, value):
        if value not in SEVERITIES:
            raise ValueError(f"must be one of {', '.join(SEVERITIES)}")
        return value

    @field_validator("evidence")
    @classmethod
    def valid_evidence(cls, value, info: ValidationInfo):
        if value not in EVIDENCES:
            raise ValueError("choose what the link is: official, lab_test or report")
        if value in ("official", "lab_test") and source_kind(info.data.get("source_url")) != "official":
            raise ValueError(
                "an official notice or official lab result must link to a government, Parliament or WHO website; "
                "choose report for a news or other link"
            )
        if value == "lab_test" and not info.data.get("reason"):
            raise ValueError("say what the laboratory found, in the reason")
        return value

    @field_validator("all_lots", mode="before")
    @classmethod
    def truthy(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(value)

    def to_record(self) -> AlertRecord:
        lots: list[LotRef] = []
        for line in re.split(r"[;\n]", self.batches or ""):
            line = line.strip()
            if not line:
                continue
            batch, _, expiry = line.partition(",")
            key = lot_key(batch)
            if key:
                lots.append(LotRef(lot=batch.strip(), key=key, expiry_text=expiry.strip() or None))
            else:
                lots += extract_lots(line).lots
        return AlertRecord(
            external_id=self.external_id or f"curated-{uuid.uuid4()}",
            title=self.title,
            severity=self.severity,
            alert_type=self.alert_type,
            source_url=self.source_url,
            published_at=self.published_at,
            classification=None,
            product_names=_split(self.product_names),
            active_ingredients=_split(self.active_ingredients),
            manufacturer=self.manufacturer,
            product_codes=_split(self.product_codes),
            reason=self.reason,
            lots=lots,
            all_lots=self.all_lots,
            lots_text=", ".join(lot.lot for lot in lots) or None,
            regulator=self.regulator,
            country_code=self.country_code.upper() if self.country_code else None,
            category=self.category,
            evidence=self.evidence,
            raw={"curated": True},
        )


CURATED_CSV_COLUMNS = [
    "category",
    "regulator",
    "country_code",
    "title",
    "alert_type",
    "severity",
    "product_names",
    "active_ingredients",
    "manufacturer",
    "product_codes",
    "batches",
    "all_lots",
    "reason",
    "published_at",
    "source_url",
    "external_id",
    "evidence",
]


def parse_curated_csv(raw: bytes) -> tuple[list[CuratedAlertInput], list[str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    missing = {"regulator", "title", "alert_type", "severity", "product_names", "published_at", "source_url", "evidence"} - set(
        reader.fieldnames or []
    )
    if missing:
        return [], [f"Missing column(s): {', '.join(sorted(missing))}. Expected: {', '.join(CURATED_CSV_COLUMNS)}"]
    rows, errors = [], []
    for line_number, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        try:
            rows.append(CuratedAlertInput(**{k: v for k, v in row.items() if k in CURATED_CSV_COLUMNS}))
        except ValidationError as exc:
            errors.append(
                f"Line {line_number}: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            )
        if len(rows) + len(errors) > 2000:
            errors.append("Only 2,000 alerts can be imported at once.")
            break
    return rows, errors


def publish_curated(db: Session, data: CuratedAlertInput, user_id: uuid.UUID | None = None) -> tuple[Alert, bool, bool, int]:
    """Store a hand-added alert, match changed medicine alerts to stock and notify workspaces.

    Returns (alert, created, changed, new matches). Commits.
    """
    ensure_sources(db)
    source = db.get(Source, "curated")
    alert, created, changed = upsert_alert(db, source, data.to_record(), created_by=user_id)
    db.commit()
    if changed and not created:
        rescore_open_matches(db, alert_ids=[alert.id])
    new_matches = match_alerts(db, [alert.id]) if changed else []
    db.commit()
    if new_matches:
        notify_new_matches(db, new_matches)
        db.commit()
    return alert, created, changed, len(new_matches)
