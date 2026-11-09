"""Store Project Passports: merge notices into one record per work, track changes, link works to programmes."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from ..ingest.base import http_client
from ..models import InfraDocument, InfraEvent, InfraParty, InfraProject, InfraProjectParty, IngestionRun, Source
from .sources import REGISTRY, DocumentData, EventData, InfraSource, ProjectData

log = logging.getLogger("batchlight.infra")

# Fields copied from a record. A value the new notice does not give never erases one an earlier notice gave.
FIELDS = (
    "level", "title", "description", "asset_type", "state_slugs", "state_basis", "parent_external_id", "funder", "borrower",
    "implementing_agency", "tendering_body", "budget_amount", "budget_currency", "funder_amount", "funder_currency",
    "contract_amount", "contract_currency", "procurement_method", "bids_evaluated", "bids_rejected", "approved_on",
    "tendered_on", "bid_deadline_on", "awarded_on", "duration_text", "promised_completion_on", "promised_basis", "status",
    "tender_url",
)  # fmt: skip
TRACKED = {
    "promised_completion_on": "Planned date changed",
    "contract_amount": "Contract price changed",
    "budget_amount": "Total cost changed",
    "status": "Status changed",
}
INFRA_SINCE = date(2018, 1, 1)
OVERLAP_DAYS = 30
_SUFFIXES = [(r"\bPVT\b\.?", "PRIVATE"), (r"\bLTD\b\.?", "LIMITED"), (r"\bCO\b\.?", "COMPANY"), (r"&", " AND ")]


def party_key(name: str) -> str:
    """'M/s. ABC Constructions Pvt. Ltd.' and 'ABC CONSTRUCTIONS PRIVATE LIMITED' share one key."""
    value = name.upper()
    value = re.sub(r"^\s*(?:M/S\.?|MESSRS\.?|MESARS\.?|M/S)\s*", "", value)
    for pattern, replacement in _SUFFIXES:
        value = re.sub(pattern, replacement, value)
    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", value).split())[:300]


def ensure_infra_sources(session: Session) -> None:
    existing = {s.id: s for s in session.scalars(select(Source).where(Source.id.in_(list(REGISTRY))))}
    for adapter in REGISTRY.values():
        row = existing.get(adapter.id)
        if row is None:
            row = Source(id=adapter.id, enabled=True, automated=True)
            session.add(row)
        row.name, row.regulator, row.country_code = adapter.name, adapter.publisher[:100], "IN"
        row.homepage_url, row.category, row.automated = adapter.homepage_url, "infrastructure", True
    session.flush()


def _fmt(value) -> str:
    if value is None:
        return "not given"
    if isinstance(value, date):
        return f"{value.day} {value:%b %Y}"
    if isinstance(value, float | int) or hasattr(value, "quantize"):
        return f"{float(value):,.2f}"
    return str(value)


def _plain(value):
    return float(value) if hasattr(value, "quantize") or isinstance(value, float | int) and not isinstance(value, bool) else value


def _hash(project: InfraProject, contractors: list[str], events: list[str]) -> str:
    payload = {f: _plain(getattr(project, f)) for f in FIELDS}
    payload["contractors"], payload["events"] = sorted(contractors), sorted(events)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def upsert_project(session: Session, source: Source, record: ProjectData) -> tuple[InfraProject, bool, bool]:
    """Insert or merge one record. Returns (project, created, changed)."""
    project = session.scalar(
        select(InfraProject).where(InfraProject.source_id == source.id, InfraProject.external_id == record.external_id)
    )
    created = project is None
    if created:
        project = InfraProject(source_id=source.id, external_id=record.external_id, source_url=record.source_url, content_hash="")
        session.add(project)
    changes = []
    for name in FIELDS:
        new = getattr(record, name)
        if new in (None, "", []):
            continue
        old = getattr(project, name)
        if name == "state_slugs" and old and project.state_basis == "source" and record.state_basis != "source":
            continue
        if not created and name in TRACKED and old not in (None, "") and str(old) != str(new):
            if not (isinstance(old, float | int) or hasattr(old, "quantize")) or abs(float(old) - float(new)) >= 0.01:
                changes.append((name, old, new))
        setattr(project, name, new)
    if record.level == "programme" or created:
        project.source_url = record.source_url
    if record.data:
        merged = dict(project.data or {}) if not created else {}
        for key, value in record.data.items():
            merged[key] = {**merged.get(key, {}), **value} if isinstance(value, dict) else value
        project.data = merged
    known = {p.party.key for p in project.parties if p.role == "contractor"} if not created else set()
    for name in record.contractors:
        key = party_key(name)
        if not key or key in known:
            continue
        party = session.scalar(select(InfraParty).where(InfraParty.key == key, InfraParty.kind == "contractor"))
        if party is None:
            party = InfraParty(key=key, name=" ".join(name.split())[:300], kind="contractor")
            session.add(party)
            session.flush()
        project.parties.append(InfraProjectParty(party=party, role="contractor"))
        known.add(key)
    if record.contractors:
        project.contractors_text = "; ".join(dict.fromkeys(" ".join(c.split()) for c in record.contractors))
    existing_events = {e.key: e for e in project.events} if not created else {}
    for event in record.events:
        _add_event(project, existing_events, event)
    today = datetime.now(UTC).date()
    for name, old, new in changes:
        label = TRACKED[name]
        _add_event(
            project,
            existing_events,
            EventData(
                f"change-{name}-{today.isoformat()}-{_fmt(new)}"[:200],
                "change",
                today,
                f"{label}: from {_fmt(old)} to {_fmt(new)}",
                "Seen when we compared the source record with our last copy.",
                "official",
                record.events[0].source_name if record.events else "Source record",
                record.source_url,
            ),
        )
    digest = _hash(project, [p.party.key for p in project.parties], [e.key for e in project.events])
    changed = created or digest != project.content_hash
    if changed:
        project.content_hash = digest
        project.updated_at = datetime.now(UTC)
    session.flush()
    return project, created, changed


def _add_event(project: InfraProject, existing: dict[str, InfraEvent], event: EventData) -> None:
    row = existing.get(event.key)
    if row is None:
        row = InfraEvent(key=event.key[:200])
        project.events.append(row)
        existing[event.key] = row
    row.kind, row.occurred_on, row.title, row.detail = event.kind, event.occurred_on, event.title, event.detail
    row.evidence, row.source_name, row.source_url = event.evidence, event.source_name[:200], event.source_url


def upsert_document(session: Session, source: Source, record: DocumentData) -> tuple[InfraDocument, bool]:
    doc = session.scalar(
        select(InfraDocument).where(InfraDocument.source_id == source.id, InfraDocument.external_id == record.external_id)
    )
    created = doc is None
    if created:
        doc = InfraDocument(source_id=source.id, external_id=record.external_id)
        session.add(doc)
    for name in ("kind", "title", "url", "report_type", "sectors", "state_slugs", "published_on"):
        setattr(doc, name, getattr(record, name))
    session.flush()
    return doc, created


def link_projects(session: Session) -> None:
    """Attach works to their programme and let a single-state programme give its state to works that name none."""
    session.execute(
        text(
            "UPDATE infra_projects w SET parent_id = p.id FROM infra_projects p "
            "WHERE w.level = 'work' AND p.level = 'programme' AND w.parent_external_id = p.external_id "
            "AND w.parent_id IS DISTINCT FROM p.id"
        )
    )
    session.execute(
        text(
            "UPDATE infra_projects w SET state_slugs = p.state_slugs, state_basis = 'programme' FROM infra_projects p "
            "WHERE w.parent_id = p.id AND cardinality(w.state_slugs) = 0 AND cardinality(p.state_slugs) = 1"
        )
    )


def run_infra_source(factory: sessionmaker[Session], adapter: InfraSource, client: httpx.Client | None = None) -> IngestionRun:
    owns_client = client is None
    client = client or http_client()
    with factory() as session:
        ensure_infra_sources(session)
        source = session.get(Source, adapter.id)
        run = IngestionRun(source_id=source.id, status="running")
        session.add(run)
        source.last_run_at = datetime.now(UTC)
        session.commit()
        since = INFRA_SINCE
        if source.last_success_at:
            since = max(INFRA_SINCE, source.last_success_at.date() - timedelta(days=OVERLAP_DAYS))
        try:
            for record in adapter.fetch(client, since):
                run.fetched += 1
                if isinstance(record, DocumentData):
                    _, created = upsert_document(session, source, record)
                    run.created += int(created)
                else:
                    _, created, changed = upsert_project(session, source, record)
                    run.created += int(created)
                    run.updated += int(changed and not created)
                if run.fetched % 200 == 0:
                    session.commit()
            link_projects(session)
            run.status, source.last_success_at, source.last_error = "succeeded", datetime.now(UTC), None
        except Exception as exc:  # noqa: BLE001 - recorded on the run and the source
            session.rollback()
            run = session.merge(run)
            source = session.get(Source, adapter.id)
            run.status, source.last_error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            log.exception("infrastructure source %s failed", adapter.id)
        finally:
            run.finished_at = datetime.now(UTC)
            session.commit()
            if owns_client:
                client.close()
        session.refresh(run)
        session.expunge(run)
        return run


def due_sources(session: Session) -> list[InfraSource]:
    ensure_infra_sources(session)
    now = datetime.now(UTC)
    due = []
    for adapter in REGISTRY.values():
        source = session.get(Source, adapter.id)
        if source.enabled and (source.last_run_at is None or now - source.last_run_at >= timedelta(hours=adapter.interval_hours)):
            due.append(adapter)
    return due
