"""Consumer reports of suspicious food. Reports are unverified and are never published as findings."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import REPORT_PROBLEMS, FoodReport
from .verify import digits

PROBLEM_LABELS = {
    "looks_wrong": "It looks, smells or tastes wrong",
    "made_ill": "Someone became ill after eating it",
    "foreign_object": "There was something in it that should not be",
    "label_problem": "The label is missing, wrong or misleading",
    "suspected_fake": "I think it is fake or not the real brand",
    "expired_or_tampered": "It was expired, damaged or the seal was broken",
    "other": "Something else",
}
assert set(PROBLEM_LABELS) == set(REPORT_PROBLEMS)
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I confusion when read aloud


class ReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    problem: str
    product_name: str = Field(min_length=2, max_length=300)
    brand: str | None = Field(default=None, max_length=200)
    batch: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, max_length=30)
    fssai_licence: str | None = Field(default=None, max_length=30)
    purchased_at: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    country_code: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    description: str = Field(min_length=10, max_length=4000)
    contact_email: str | None = Field(default=None, max_length=320)

    @field_validator("*", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("problem", mode="before")
    @classmethod
    def known_problem(cls, value):  # runs before blank_to_none, so an empty choice gets a helpful message
        if value not in PROBLEM_LABELS:
            raise ValueError("Choose what went wrong")
        return value

    @field_validator("barcode", "fssai_licence")
    @classmethod
    def only_digits(cls, value):
        if value is None:
            return None
        number = digits(value)
        if not number:
            raise ValueError("Use the digits printed on the pack")
        return number[:20]

    @field_validator("contact_email")
    @classmethod
    def valid_email(cls, value):
        if value is None:
            return None
        try:
            return validate_email(value, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            raise ValueError("Enter a valid email address, or leave it blank") from None


@lru_cache
def _fallback_secret() -> str:
    return secrets.token_hex(32)


def ip_fingerprint(ip: str | None) -> str | None:
    """A keyed hash of the reporter's IP address: enough to rate-limit, without storing the address."""
    if not ip:
        return None
    secret = get_settings().secret_key or _fallback_secret()
    return hashlib.sha256(f"{secret}:{ip}".encode()).hexdigest()


def over_limit(session: Session, ip_hash: str | None) -> bool:
    if ip_hash is None:
        return False
    since = datetime.now(UTC) - timedelta(hours=1)
    count = session.scalar(
        select(func.count()).select_from(FoodReport).where(FoodReport.ip_hash == ip_hash, FoodReport.created_at >= since)
    )
    return (count or 0) >= get_settings().report_limit_per_hour


def new_reference(session: Session) -> str:
    while True:
        reference = "FR-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(8))
        if session.scalar(select(FoodReport.id).where(FoodReport.reference == reference)) is None:
            return reference


def create_report(session: Session, data: ReportInput, ip: str | None, user_id: uuid.UUID | None) -> FoodReport:
    report = FoodReport(reference=new_reference(session), ip_hash=ip_fingerprint(ip), user_id=user_id, **data.model_dump())
    session.add(report)
    session.flush()
    return report
