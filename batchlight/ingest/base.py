"""Ingestion pipeline shared by every regulator source."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..lots import LotRef
from ..models import Alert, AlertLot, IngestionRun, Source
from ..text import manufacturer_key, name_tokens, truncate

log = logging.getLogger("batchlight.ingest")


@dataclass
class AlertRecord:
    """A regulator alert normalised to Batchlight's schema, before storage."""

    external_id: str
    title: str
    severity: str
    alert_type: str
    source_url: str
    published_at: date | None = None
    initiated_at: date | None = None
    classification: str | None = None
    status: str | None = None
    product_description: str | None = None
    product_names: list[str] = field(default_factory=list)
    active_ingredients: list[str] = field(default_factory=list)
    manufacturer: str | None = None
    product_codes: list[str] = field(default_factory=list)
    reason: str | None = None
    distribution: str | None = None
    lots: list[LotRef] = field(default_factory=list)
    all_lots: bool = False
    lots_text: str | None = None
    regulator: str | None = None  # overrides the source's regulator (curated alerts)
    country_code: str | None = None  # overrides the source's country (curated alerts)
    partial: bool = False  # summary only: keep details (batches, company, codes) already stored
    raw: dict[str, Any] = field(default_factory=dict)

    def content_hash(self) -> str:
        payload = asdict(self)
        payload.pop("raw")
        payload.pop("partial")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class SourceAdapter(ABC):
    id: str
    name: str
    regulator: str
    country_code: str | None
    homepage_url: str
    automated: bool = True

    @abstractmethod
    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        """Yield alerts published or updated on or after ``since``."""

    def prepare(self, session: Session, source: Source) -> None:  # noqa: B027 - optional hook
        """Called before ``fetch`` so an adapter can look at what is already stored."""


class ManualSource(SourceAdapter):
    """Alerts entered by curators from PDF or image notices (CDSCO, NAFDAC, ...)."""

    id = "curated"
    name = "Curated alerts (entered from regulator notices)"
    regulator = "Various regulators"
    country_code = None
    homepage_url = "https://github.com/greenwichg/Humanity-Labs/blob/main/docs/PRODUCT.md"
    automated = False

    def fetch(self, client: httpx.Client, since: date) -> Iterator[AlertRecord]:
        return iter(())


REGISTRY: dict[str, SourceAdapter] = {}
MAX_LOTS_PER_ALERT = 10_000


def register(adapter: SourceAdapter) -> SourceAdapter:
    REGISTRY[adapter.id] = adapter
    return adapter


register(ManualSource())


def http_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """HTTP client for regulator sources.

    The default transport honours HTTPS_PROXY and SSL_CERT_FILE from the
    environment; tests pass a mock transport.
    """
    settings = get_settings()
    options: dict[str, Any] = {"transport": transport} if transport is not None else {}
    return httpx.Client(
        headers={"User-Agent": settings.http_user_agent, "Accept-Language": "en"},
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        **options,
    )


RETRY_STATUSES = (429, 500, 502, 503, 504)


def get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    attempts: int = 3,
    allow_404: bool = False,
    backoff: float = 1.0,
) -> httpx.Response | None:
    """GET with exponential backoff on connection errors, 429 and 5xx. Returns None for an allowed 404."""
    delay = backoff
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, params=params)
        except httpx.TransportError:
            if attempt == attempts:
                raise
            time.sleep(delay)
            delay *= 2
            continue
        if response.status_code == 404 and allow_404:
            return None
        if response.status_code in RETRY_STATUSES and attempt < attempts:
            time.sleep(delay)
            delay *= 2
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable")


def ensure_sources(session: Session) -> None:
    """Insert or refresh a row for every registered adapter."""
    existing = {s.id: s for s in session.scalars(select(Source))}
    for adapter in REGISTRY.values():
        row = existing.get(adapter.id)
        if row is None:
            settings = get_settings()
            session.add(
                Source(
                    id=adapter.id,
                    name=adapter.name,
                    regulator=adapter.regulator,
                    country_code=adapter.country_code,
                    homepage_url=adapter.homepage_url,
                    automated=adapter.automated,
                    enabled=not adapter.automated or adapter.id in settings.sources,
                )
            )
        else:
            row.name, row.regulator, row.country_code = adapter.name, adapter.regulator, adapter.country_code
            row.homepage_url, row.automated = adapter.homepage_url, adapter.automated
    session.flush()


def upsert_alert(
    session: Session, source: Source, record: AlertRecord, created_by: uuid.UUID | None = None
) -> tuple[Alert, bool, bool]:
    """Insert or update an alert. Returns (alert, created, changed)."""
    alert = session.scalar(select(Alert).where(Alert.source_id == source.id, Alert.external_id == record.external_id))
    if record.partial and alert is not None:
        _keep_stored_details(record, alert)
    _normalise(record)
    digest = record.content_hash()
    created = alert is None
    if alert is not None and alert.content_hash == digest:
        return alert, False, False
    if alert is None:
        alert = Alert(source_id=source.id, external_id=record.external_id, created_by=created_by)
        session.add(alert)
    alert.regulator = record.regulator or source.regulator
    alert.country_code = record.country_code or source.country_code or None
    alert.alert_type = record.alert_type
    alert.severity = record.severity
    alert.classification = truncate(record.classification, 100) or None
    alert.status = truncate(record.status, 100) or None
    alert.title = truncate(record.title, 500) or record.external_id
    alert.product_description = record.product_description
    alert.product_names = record.product_names
    alert.active_ingredients = record.active_ingredients
    alert.manufacturer = record.manufacturer.strip() if record.manufacturer else None
    alert.manufacturer_key = manufacturer_key(record.manufacturer) or None
    alert.product_codes = record.product_codes
    alert.name_tokens = name_tokens(*alert.product_names, *alert.active_ingredients)
    alert.reason = record.reason
    alert.distribution = record.distribution
    alert.lots_text = record.lots_text
    alert.all_lots = record.all_lots
    alert.published_at = record.published_at
    alert.initiated_at = record.initiated_at
    alert.source_url = record.source_url
    alert.content_hash = digest
    alert.raw = record.raw
    alert.updated_at = datetime.now(UTC)
    alert.lots.clear()
    seen: set[str] = set()
    if len(record.lots) > MAX_LOTS_PER_ALERT:
        log.warning("alert %s lists %s batches; keeping the first %s", record.external_id, len(record.lots), MAX_LOTS_PER_ALERT)
    for lot in record.lots[:MAX_LOTS_PER_ALERT]:
        if lot.key and lot.key not in seen:
            seen.add(lot.key)
            alert.lots.append(
                AlertLot(lot=truncate(lot.lot, 100), lot_key=lot.key[:100], expiry_text=truncate(lot.expiry_text, 100) or None)
            )
    alert.lot_count = len(alert.lots)
    session.flush()
    return alert, created, True


def _unique(values: list[str | None]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _normalise(record: AlertRecord) -> None:
    """Canonical list fields, so the content hash only changes when the content does."""
    record.product_names = [truncate(n, 300) for n in _unique(record.product_names)]
    record.active_ingredients = _unique(record.active_ingredients)
    record.product_codes = _unique(record.product_codes)


def _keep_stored_details(record: AlertRecord, alert: Alert) -> None:
    """A summary-only record must not erase details fetched by an earlier run."""
    if not record.lots and alert.lots:
        record.lots = [LotRef(lot=lot.lot, key=lot.lot_key, expiry_text=lot.expiry_text) for lot in alert.lots]
        record.lots_text = alert.lots_text
    record.all_lots = record.all_lots or alert.all_lots
    record.manufacturer = record.manufacturer or alert.manufacturer
    record.initiated_at = record.initiated_at or alert.initiated_at
    record.product_codes = list(dict.fromkeys([*record.product_codes, *alert.product_codes]))
    record.product_names = list(dict.fromkeys([*record.product_names, *alert.product_names]))
    record.raw = {**(alert.raw or {}), **record.raw, "detail_fetched": (alert.raw or {}).get("detail_fetched", False)}


def run_source(
    factory: sessionmaker[Session], adapter: SourceAdapter, client: httpx.Client | None = None, notify: bool = True
) -> IngestionRun:
    """Fetch, upsert, match and notify for one source, recording an ingestion run."""
    from ..matching import match_alerts
    from ..services.notifications import notify_new_matches

    settings = get_settings()
    owns_client = client is None
    client = client or http_client()
    with factory() as session:
        ensure_sources(session)
        source = session.get(Source, adapter.id)
        assert source is not None
        run = IngestionRun(source_id=source.id, status="running")
        session.add(run)
        source.last_run_at = datetime.now(UTC)
        session.commit()
        since = settings.ingest_since
        if source.last_success_at:
            since = max(settings.ingest_since, source.last_success_at.date() - timedelta(days=settings.ingest_overlap_days))
        try:
            adapter.prepare(session, source)
            changed: list[uuid.UUID] = []
            for record in adapter.fetch(client, since):
                run.fetched += 1
                alert, created, did_change = upsert_alert(session, source, record)
                run.created += int(created)
                run.updated += int(did_change and not created)
                if did_change:
                    changed.append(alert.id)
                if run.fetched % 200 == 0:
                    session.commit()
            session.commit()
            new_matches = match_alerts(session, changed)
            run.matched = len(new_matches)
            session.commit()
            if notify and new_matches:
                notify_new_matches(session, new_matches)
                session.commit()
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            source.last_success_at = run.finished_at
            source.last_error = None
            session.commit()
            log.info(
                "source %s: fetched=%s created=%s updated=%s matched=%s",
                source.id,
                run.fetched,
                run.created,
                run.updated,
                run.matched,
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the run and re-raised for the caller
            session.rollback()
            run = session.merge(run)
            source = session.get(Source, adapter.id)
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            source.last_error = run.error
            session.commit()
            log.exception("source %s failed", adapter.id)
            raise
        finally:
            if owns_client:
                client.close()
        session.refresh(run)
        session.expunge(run)
        return run


def parse_date(value: str | None, *formats: str) -> date | None:
    """Parse a date in one of ``formats`` (default YYYYMMDD and YYYY-MM-DD), or ISO 8601."""
    if not value:
        return None
    value = value.strip()
    for fmt in formats or ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None
