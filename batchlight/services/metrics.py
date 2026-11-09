"""Outcome metrics: what actually happened because of Batchlight, not page views."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import ACTION_OUTCOMES, Alert, FoodReport, Holding, IngestionRun, Match, Organization, Source, TestingLab


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def match_metrics(session: Session, org_id: uuid.UUID | None = None) -> dict:
    scope = [Match.org_id == org_id] if org_id else []
    now = datetime.now(UTC)
    total = session.scalar(select(func.count()).select_from(Match).where(*scope)) or 0
    open_ = session.scalar(select(func.count()).select_from(Match).where(*scope, Match.status == "open")) or 0
    resolved = session.execute(
        select(Match.created_at, Match.resolved_at, Match.outcome).where(*scope, Match.status == "resolved")
    ).all()
    hours = [(r.resolved_at - r.created_at).total_seconds() / 3600 for r in resolved if r.resolved_at]
    false_matches = sum(1 for r in resolved if r.outcome == "false_match")
    old_enough = (
        session.scalar(select(func.count()).select_from(Match).where(*scope, Match.created_at <= now - timedelta(hours=72))) or 0
    )
    acted_72h = (
        session.scalar(
            select(func.count())
            .select_from(Match)
            .where(
                *scope,
                Match.created_at <= now - timedelta(hours=72),
                Match.status == "resolved",
                Match.outcome != "false_match",
                Match.resolved_at <= Match.created_at + timedelta(hours=72),
            )
        )
        or 0
    )
    units = (
        session.scalar(select(func.coalesce(func.sum(Match.units_affected), 0)).where(*scope, Match.outcome.in_(ACTION_OUTCOMES)))
        or 0
    )
    return {
        "matches_total": total,
        "matches_open": open_,
        "matches_resolved": len(resolved),
        "median_hours_to_action": _median(hours),
        "action_rate_72h": (acted_72h / old_enough) if old_enough else None,
        "matches_eligible_72h": old_enough,
        "units_removed": int(units),
        "false_match_rate": (false_matches / len(resolved)) if resolved else None,
    }


def alert_metrics(session: Session, category: str = "medicine") -> dict:
    in_category = Alert.category == category
    total = session.scalar(select(func.count()).select_from(Alert).where(in_category)) or 0
    with_batches = (
        session.scalar(
            select(func.count()).select_from(Alert).where(in_category, or_(Alert.all_lots.is_(True), Alert.lot_count > 0))
        )
        or 0
    )
    batches = session.scalar(select(func.coalesce(func.sum(Alert.lot_count), 0)).where(in_category)) or 0
    regulators = session.scalar(select(func.count(func.distinct(Alert.regulator))).where(in_category)) or 0
    # Latency: alerts that arrived through incremental runs, i.e. after each source's first successful run.
    first_success = (
        select(IngestionRun.source_id, func.min(IngestionRun.finished_at).label("first"))
        .where(IngestionRun.status == "succeeded")
        .group_by(IngestionRun.source_id)
        .subquery()
    )
    rows = session.execute(
        select(Alert.created_at, Alert.published_at)
        .join(first_success, first_success.c.source_id == Alert.source_id)
        .where(in_category, Alert.published_at.is_not(None), Alert.created_at > first_success.c.first + timedelta(hours=1))
    ).all()
    lags = [max(0.0, (r.created_at.date() - r.published_at).days) for r in rows]
    by_source = session.execute(
        select(Source.name, func.count(Alert.id))
        .join(Alert, Alert.source_id == Source.id)
        .where(in_category)
        .group_by(Source.name)
        .order_by(func.count(Alert.id).desc())
    ).all()
    by_severity = dict(session.execute(select(Alert.severity, func.count()).where(in_category).group_by(Alert.severity)).all())
    return {
        "alerts_total": total,
        "alerts_with_batches": with_batches,
        "batch_share": (with_batches / total) if total else None,
        "batches_total": batches,
        "regulators": regulators,
        "median_days_publication_to_batchlight": _median(lags),
        "latency_sample": len(lags),
        "by_source": by_source,
        "by_severity": by_severity,
    }


def food_metrics(session: Session) -> dict:
    by_status = dict(session.execute(select(FoodReport.status, func.count()).group_by(FoodReport.status)).all())
    return {
        "food_alerts": session.scalar(select(func.count()).select_from(Alert).where(Alert.category == "food")) or 0,
        "reports_total": sum(by_status.values()),
        "reports_reviewed": sum(n for status, n in by_status.items() if status != "new"),
        "reports_forwarded": by_status.get("forwarded", 0),
        "labs": session.scalar(select(func.count()).select_from(TestingLab).where(TestingLab.active.is_(True))) or 0,
    }


def reach_metrics(session: Session) -> dict:
    return {
        "workspaces": session.scalar(select(func.count()).select_from(Organization)) or 0,
        "countries": session.scalar(select(func.count(func.distinct(Organization.country_code)))) or 0,
        "holdings": session.scalar(select(func.count()).select_from(Holding).where(Holding.archived_at.is_(None))) or 0,
        "holdings_with_batch": session.scalar(
            select(func.count()).select_from(Holding).where(and_(Holding.archived_at.is_(None), Holding.lot_key.is_not(None)))
        )
        or 0,
    }
