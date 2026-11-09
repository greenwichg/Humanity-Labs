"""Read-side queries for alerts: search, batch check and manufacturer histories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..matching import AlertFacts, Decision, HoldingFacts, score
from ..models import Alert, AlertLot, Source
from ..text import lot_key, manufacturer_key, manufacturer_slug, name_tokens, strengths

PER_PAGE = 25


@dataclass
class Page:
    items: list
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def looks_like_batch(q: str) -> bool:
    return bool(q) and " " not in q.strip() and any(ch.isdigit() for ch in q) and 3 <= len(lot_key(q)) <= 30


def search_alerts(
    session: Session,
    q: str = "",
    source: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    page: int = 1,
    per_page: int = PER_PAGE,
) -> Page:
    stmt = select(Alert)
    q = (q or "").strip()[:200]
    if q:
        conditions = [Alert.search.op("@@")(func.plainto_tsquery("simple", q)), Alert.title.ilike(f"%{q}%")]
        if looks_like_batch(q):
            conditions.append(Alert.id.in_(select(AlertLot.alert_id).where(AlertLot.lot_key == lot_key(q))))
        stmt = stmt.where(or_(*conditions))
    if source:
        stmt = stmt.where(Alert.source_id == source)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    items = session.scalars(
        stmt.order_by(Alert.published_at.desc().nulls_last(), Alert.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .options(selectinload(Alert.source))
    ).all()
    return Page(list(items), total, page, per_page)


def first_lots(session: Session, alert_ids: list, limit: int = 100) -> dict:
    """Up to ``limit`` batches per alert, without loading alerts that list thousands of serial numbers."""
    if not alert_ids:
        return {}
    ranked = (
        select(
            AlertLot.alert_id,
            AlertLot.lot,
            AlertLot.expiry_text,
            func.row_number().over(partition_by=AlertLot.alert_id, order_by=AlertLot.id).label("rn"),
        )
        .where(AlertLot.alert_id.in_(alert_ids))
        .subquery()
    )
    out: dict = {alert_id: [] for alert_id in alert_ids}
    for row in session.execute(
        select(ranked.c.alert_id, ranked.c.lot, ranked.c.expiry_text)
        .where(ranked.c.rn <= limit)
        .order_by(ranked.c.alert_id, ranked.c.rn)
    ):
        out[row.alert_id].append((row.lot, row.expiry_text))
    return out


@dataclass
class CheckResult:
    alert: Alert
    decision: Decision | None  # None when only the batch number matched and no product was given
    batch_match: bool


def check_batch(session: Session, batch: str, product: str | None = None, manufacturer: str | None = None) -> list[CheckResult]:
    """Public batch check. With a product name, results are scored like a stock match."""
    key = lot_key(batch)
    holding = HoldingFacts(
        name_tokens=set(name_tokens(product)),
        lot_key=key or None,
        manufacturer=manufacturer or None,
        product_code=None,
        strengths=strengths(product),
    )
    candidates: dict = {}
    if key:
        for alert in session.scalars(
            select(Alert)
            .join(AlertLot)
            .where(AlertLot.lot_key == key)
            .options(selectinload(Alert.lots), selectinload(Alert.source))
        ):
            candidates[alert.id] = alert
    if product and holding.name_tokens:
        for alert in session.scalars(
            select(Alert)
            .where(Alert.name_tokens.overlap(sorted(holding.name_tokens)))
            .order_by(Alert.published_at.desc().nulls_last())
            .limit(300)
            .options(selectinload(Alert.lots), selectinload(Alert.source))
        ):
            candidates.setdefault(alert.id, alert)
    results = []
    for alert in candidates.values():
        batch_match = bool(key) and key in {lot.lot_key for lot in alert.lots}
        if product:
            decision = score(AlertFacts.from_alert(alert), holding, strict=False)
            if decision:
                results.append(CheckResult(alert, decision, batch_match))
        elif batch_match:
            results.append(CheckResult(alert, None, True))
    order = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "info": 4}
    results.sort(
        key=lambda r: (
            not r.batch_match,
            -(r.decision.confidence if r.decision else 100),
            order.get(r.alert.severity, 9),
            -(r.alert.published_at or date.min).toordinal(),
        )
    )
    return results[:50]


def sources_status(session: Session) -> list[dict]:
    counts = dict(session.execute(select(Alert.source_id, func.count()).group_by(Alert.source_id)).all())
    return [
        {"source": s, "alerts": counts.get(s.id, 0)}
        for s in session.scalars(select(Source).order_by(Source.automated.desc(), Source.name))
        if s.enabled or counts.get(s.id)
    ]


def manufacturer_index(session: Session, q: str = "", limit: int = 100) -> list[dict]:
    stmt = (
        select(
            Alert.manufacturer_key,
            func.min(Alert.manufacturer).label("name"),
            func.count().label("alerts"),
            func.count(func.distinct(Alert.source_id)).label("sources"),
            func.sum(case((Alert.severity == "critical", 1), else_=0)).label("critical"),
            func.max(Alert.published_at).label("latest"),
        )
        .where(Alert.manufacturer_key.is_not(None), Alert.manufacturer_key != "")
        .group_by(Alert.manufacturer_key)
    )
    if q:
        stmt = stmt.where(Alert.manufacturer_key.ilike(f"%{manufacturer_key(q) or q.lower()}%"))
    rows = session.execute(stmt.order_by(func.count().desc(), func.max(Alert.published_at).desc()).limit(limit)).all()
    return [dict(row._mapping, slug=manufacturer_slug(row.manufacturer_key)) for row in rows]


def manufacturer_alerts(session: Session, slug: str) -> tuple[str | None, list[Alert]]:
    if not re.fullmatch(r"[a-z0-9-]{1,200}", slug):
        return None, []
    key = slug.replace("-", " ")
    alerts = session.scalars(
        select(Alert)
        .where(func.regexp_replace(Alert.manufacturer_key, "[^a-z0-9]+", " ", "g") == key)
        .order_by(Alert.published_at.desc().nulls_last())
        .limit(500)
        .options(selectinload(Alert.source))
    ).all()
    return (alerts[0].manufacturer if alerts else None), list(alerts)
