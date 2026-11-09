"""Read-side queries for alerts: search, batch check and manufacturer histories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from ..food.verify import barcode_variants
from ..matching import Decision, HoldingFacts, facts_for_holding, score
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
    category: str | None = "medicine",
    country: str | None = None,
) -> Page:
    """Search alerts by text, batch code or barcode. ``category=None`` searches every category."""
    stmt = select(Alert)
    if category:
        stmt = stmt.where(Alert.category == category)
    q = (q or "").strip()[:200]
    if q:
        conditions = [Alert.search.op("@@")(func.plainto_tsquery("simple", q)), Alert.title.ilike(f"%{q}%")]
        if looks_like_batch(q):
            conditions.append(Alert.id.in_(select(AlertLot.alert_id).where(AlertLot.lot_key == lot_key(q))))
        number = re.sub(r"[\s-]", "", q)
        if number.isdigit() and 8 <= len(number) <= 14:
            conditions.append(Alert.product_codes.overlap(barcode_variants(number)))
        stmt = stmt.where(or_(*conditions))
    if country:
        stmt = stmt.where(Alert.country_code == country)
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
    """Up to ``limit`` batches per alert, in listed order.

    A LATERAL ... LIMIT over the (alert_id, id) index reads only the rows it
    returns, even for device recalls that list thousands of serial numbers.
    """
    if not alert_ids:
        return {}
    out: dict = {alert_id: [] for alert_id in alert_ids}
    rows = session.execute(
        text(
            "SELECT a.id AS alert_id, l.lot, l.expiry_text FROM unnest(CAST(:ids AS uuid[])) AS a(id) "
            "CROSS JOIN LATERAL (SELECT lot, expiry_text, id FROM alert_lots WHERE alert_id = a.id ORDER BY id LIMIT :n) l "
            "ORDER BY a.id, l.id"
        ),
        {"ids": list(dict.fromkeys(alert_ids)), "n": limit},
    )
    for row in rows:
        out[row.alert_id].append((row.lot, row.expiry_text))
    return out


@dataclass
class CheckResult:
    alert: Alert
    decision: Decision | None  # None when only the batch number matched and no product was given
    batch_match: bool


def check_batch(
    session: Session, batch: str, product: str | None = None, manufacturer: str | None = None, category: str = "medicine"
) -> list[CheckResult]:
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
    listed: set = set()
    if key:
        for alert in session.scalars(
            select(Alert)
            .join(AlertLot)
            .where(AlertLot.lot_key == key, Alert.category == category)
            .options(selectinload(Alert.source))
        ):
            candidates[alert.id] = alert
            listed.add(alert.id)
    if product and holding.name_tokens:
        for alert in session.scalars(
            select(Alert)
            .where(Alert.name_tokens.overlap(sorted(holding.name_tokens)), Alert.category == category)
            .order_by(Alert.published_at.desc().nulls_last())
            .limit(300)
            .options(selectinload(Alert.source))
        ):
            candidates.setdefault(alert.id, alert)
    results = []
    for alert in candidates.values():
        batch_match = alert.id in listed
        if product:
            decision = score(facts_for_holding(alert, key, batch_match), holding, strict=False)
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


def sources_status(session: Session, category: str | None = "medicine") -> list[dict]:
    """Sources with alert counts. The shared curated source is counted per category."""
    counts_stmt = select(Alert.source_id, func.count()).group_by(Alert.source_id)
    if category:
        counts_stmt = counts_stmt.where(Alert.category == category)
    counts = dict(session.execute(counts_stmt).all())
    rows = []
    for s in session.scalars(select(Source).order_by(Source.automated.desc(), Source.name)):
        if category and s.automated and s.category != category:
            continue
        if s.enabled or counts.get(s.id):
            rows.append({"source": s, "alerts": counts.get(s.id, 0)})
    return rows


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
        .where(Alert.manufacturer_key.is_not(None), Alert.manufacturer_key != "", Alert.category == "medicine")
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
        .where(func.regexp_replace(Alert.manufacturer_key, "[^a-z0-9]+", " ", "g") == key, Alert.category == "medicine")
        .order_by(Alert.published_at.desc().nulls_last())
        .limit(500)
        .options(selectinload(Alert.source))
    ).all()
    return (alerts[0].manufacturer if alerts else None), list(alerts)
