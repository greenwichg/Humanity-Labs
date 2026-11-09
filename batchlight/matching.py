"""Match alerts to holdings.

Scoring is a pure function (``score``) so it can be unit-tested and explained.
The database functions find candidates with indexed queries and record
matches idempotently.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .models import Alert, AlertLot, Holding, Match
from .text import codes_match, manufacturer_similarity, strengths

FALSIFIED_TYPES = {"falsified", "substandard"}


@dataclass
class AlertFacts:
    name_tokens: set[str]
    lot_keys: set[str]
    all_lots: bool
    manufacturer: str | None
    product_codes: list[str]
    strengths: set[str]
    alert_type: str

    @classmethod
    def from_alert(cls, alert: Alert) -> AlertFacts:
        return cls(
            name_tokens=set(alert.name_tokens or []),
            lot_keys={lot.lot_key for lot in alert.lots},
            all_lots=alert.all_lots,
            manufacturer=alert.manufacturer,
            product_codes=list(alert.product_codes or []),
            strengths=strengths(*(alert.product_names or []), alert.title, alert.product_description),
            alert_type=alert.alert_type,
        )


@dataclass
class HoldingFacts:
    name_tokens: set[str]
    lot_key: str | None
    manufacturer: str | None
    product_code: str | None
    strengths: set[str]

    @classmethod
    def from_holding(cls, holding: Holding) -> HoldingFacts:
        return cls(
            name_tokens=set(holding.name_tokens or []),
            lot_key=holding.lot_key or None,
            manufacturer=holding.manufacturer,
            product_code=holding.product_code,
            strengths=strengths(holding.strength, holding.product_name),
        )


@dataclass
class Decision:
    level: str
    confidence: int
    reasons: list[str] = field(default_factory=list)


def score(alert: AlertFacts, holding: HoldingFacts) -> Decision | None:
    """Decide whether an alert affects a holding, and how confidently.

    Rules (see docs/ARCHITECTURE.md, "Matching algorithm"):

    * A batch-level match needs the batch number **and** at least one piece of
      product evidence, because short batch codes collide across products.
    * A product-level match needs strong product evidence and no conflicting
      manufacturer or strength. It applies when the alert covers all batches,
      lists none, or the holding has no batch recorded.
    * A holding whose batch is known and not on the alert's list never matches.
    """
    reasons: list[str] = []

    code_hit = bool(holding.product_code) and any(codes_match(holding.product_code, c) for c in alert.product_codes)
    overlap = holding.name_tokens & alert.name_tokens
    containment = len(overlap) / len(holding.name_tokens) if holding.name_tokens else 0.0
    name_hit = containment >= 0.5 and any(len(token) >= 4 for token in overlap)
    maker_known = bool(holding.manufacturer and alert.manufacturer)
    maker_sim = manufacturer_similarity(holding.manufacturer, alert.manufacturer) if maker_known else 0.0
    maker_hit = maker_sim >= 0.5
    maker_conflict = maker_known and not maker_hit
    strength_conflict = bool(holding.strengths and alert.strengths and not holding.strengths & alert.strengths)

    if code_hit:
        reasons.append("Product code matches the alert")
    if name_hit:
        reasons.append("Product name matches: " + ", ".join(sorted(overlap)))
    if maker_hit:
        reasons.append("Manufacturer matches")

    batch_hit = bool(holding.lot_key) and holding.lot_key in alert.lot_keys
    if batch_hit:
        if not (code_hit or name_hit or maker_hit):
            return None
        confidence = 90 + (5 if code_hit else 0) + (3 if name_hit else 0) + (2 if maker_hit else 0)
        if strength_conflict:
            confidence -= 15
            reasons.append("Strength differs from the alert: check the pack")
        reasons.insert(0, f"Batch {holding.lot_key} is listed in the alert")
        return Decision("batch", min(confidence, 99), reasons)

    if holding.lot_key and alert.lot_keys and not alert.all_lots:
        return None  # The holding's batch is known and is not affected.
    if strength_conflict:
        return None
    if maker_conflict and not code_hit:
        return None
    if not (code_hit or name_hit):
        return None

    if alert.all_lots:
        reasons.insert(0, "The alert covers all batches of this product")
        confidence = 70
    elif not alert.lot_keys:
        reasons.insert(0, "The alert does not list batch numbers here: check your batch against the source notice")
        confidence = 62
    else:
        reasons.insert(0, "No batch number is recorded for this item: check the pack against the listed batches")
        confidence = 55

    if code_hit:
        confidence += 12
    if maker_hit:
        confidence += 8
    elif not maker_known and alert.manufacturer and alert.alert_type not in FALSIFIED_TYPES:
        confidence -= 7
        reasons.append("Manufacturer is not recorded for this item")
    return Decision("product", max(40, min(confidence, 89)), reasons)


# --------------------------------------------------------------------------- persistence


def _record(session: Session, alert: Alert, holding: Holding, decision: Decision) -> uuid.UUID | None:
    stmt = (
        insert(Match)
        .values(
            org_id=holding.org_id,
            alert_id=alert.id,
            holding_id=holding.id,
            level=decision.level,
            confidence=decision.confidence,
            reasons=decision.reasons,
        )
        .on_conflict_do_nothing(constraint="uq_match_alert_holding")
        .returning(Match.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def match_alerts(session: Session, alert_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """Match the given alerts against every active holding. Returns new match ids."""
    created: list[uuid.UUID] = []
    alert_ids = list(alert_ids)
    if not alert_ids:
        return created
    alerts = session.scalars(select(Alert).where(Alert.id.in_(alert_ids)).options(selectinload(Alert.lots))).all()
    for alert in alerts:
        facts = AlertFacts.from_alert(alert)
        conditions = []
        if facts.lot_keys:
            conditions.append(Holding.lot_key.in_(facts.lot_keys))
        if facts.name_tokens:
            conditions.append(Holding.name_tokens.overlap(sorted(facts.name_tokens)))
        if not conditions:
            continue
        candidates = session.scalars(select(Holding).where(Holding.archived_at.is_(None), or_(*conditions))).all()
        for holding in candidates:
            decision = score(facts, HoldingFacts.from_holding(holding))
            if decision:
                match_id = _record(session, alert, holding, decision)
                if match_id:
                    created.append(match_id)
    return created


def match_holdings(session: Session, holdings: Iterable[Holding]) -> list[uuid.UUID]:
    """Match newly created or edited holdings against recent alerts. Returns new match ids."""
    created: list[uuid.UUID] = []
    cutoff = date.today() - timedelta(days=365 * get_settings().match_lookback_years)
    for holding in holdings:
        if holding.archived_at is not None:
            continue
        conditions = []
        if holding.lot_key:
            conditions.append(Alert.id.in_(select(AlertLot.alert_id).where(AlertLot.lot_key == holding.lot_key)))
        if holding.name_tokens:
            conditions.append(Alert.name_tokens.overlap(sorted(set(holding.name_tokens))))
        if not conditions:
            continue
        candidates = session.scalars(
            select(Alert)
            .where(or_(*conditions), or_(Alert.published_at.is_(None), Alert.published_at >= cutoff))
            .options(selectinload(Alert.lots))
        ).all()
        facts = HoldingFacts.from_holding(holding)
        for alert in candidates:
            decision = score(AlertFacts.from_alert(alert), facts)
            if decision:
                match_id = _record(session, alert, holding, decision)
                if match_id:
                    created.append(match_id)
    return created
