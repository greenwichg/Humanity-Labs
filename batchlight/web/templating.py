"""Jinja2 rendering with shared context (auth, CSRF token, flash, formatting helpers)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..food.knowledge import EVIDENCE_HELP, EVIDENCE_LABELS
from ..text import source_host, source_kind
from .auth import csrf_token, pop_flash

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
env = templates.env

SEVERITY_LABELS = {"critical": "Critical", "high": "High", "moderate": "Moderate", "low": "Low", "info": "Info"}
TYPE_LABELS = {
    "recall": "Recall",
    "falsified": "Falsified",
    "substandard": "Substandard",
    "quality_defect": "Quality defect",
    "safety_notice": "Safety notice",
}
OUTCOME_LABELS = {
    "quarantined": "Quarantined stock",
    "returned": "Returned to supplier",
    "destroyed": "Destroyed",
    "patients_notified": "Patients notified",
    "not_in_stock": "Not in stock",
    "false_match": "Not affected (false match)",
    "other": "Other action",
}
KIND_LABELS = {
    "pharmacy": "Pharmacy",
    "clinic": "Clinic",
    "hospital": "Hospital",
    "distributor": "Distributor or wholesaler",
    "household": "Household (my family's medicines)",
    "ngo": "NGO or programme",
    "other": "Other",
}


def fmt_date(value: date | datetime | None, missing: str = "Unknown") -> str:
    if value is None:
        return missing
    if isinstance(value, datetime):
        value = value.astimezone(UTC).date()
    return f"{value.day} {value:%b %Y}"


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return "Never"
    value = value.astimezone(UTC)
    return f"{value.day} {value:%b %Y, %H:%M} UTC"


def fmt_month(value: date | datetime | None, missing: str = "Date not given") -> str:
    return f"{value:%b %Y}" if value else missing


def time_ago(value: datetime | None) -> str:
    if value is None:
        return "never"
    seconds = (datetime.now(UTC) - value).total_seconds()
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = int(seconds // size)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def fmt_hours(value: float | None) -> str:
    if value is None:
        return "—"
    if value < 1:
        return f"{max(1, round(value * 60))} min"
    if value < 48:
        return f"{value:.1f} h"
    return f"{value / 24:.1f} days"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def fmt_int(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def country_flag(code: str | None) -> str:
    if not code or len(code) != 2 or not code.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def query(request: Request, **changes: Any) -> str:
    params = dict(request.query_params)
    for key, value in changes.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = str(value)
    return "?" + urlencode(params) if params else request.url.path


env.filters.update(
    month=fmt_month,
    date=fmt_date,
    datetime=fmt_datetime,
    ago=time_ago,
    hours=fmt_hours,
    pct=fmt_pct,
    int=fmt_int,
    flag=country_flag,
)
env.globals.update(
    source_kind=source_kind,
    source_host=source_host,
    EVIDENCE_LABELS=EVIDENCE_LABELS,
    EVIDENCE_HELP=EVIDENCE_HELP,
    SEVERITY_LABELS=SEVERITY_LABELS,
    TYPE_LABELS=TYPE_LABELS,
    OUTCOME_LABELS=OUTCOME_LABELS,
    KIND_LABELS=KIND_LABELS,
    version=__version__,
    query=query,
)


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    auth = getattr(request.state, "auth", None)
    ctx = {
        "auth": auth,
        "csrf_token": csrf_token(request),
        "flash": pop_flash(request),
        "unread": auth.unread if auth else 0,
        "path": request.url.path,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
