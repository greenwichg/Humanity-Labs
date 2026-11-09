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

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .models import Alert, AlertLot, Holding, Match
from .text import codes_match, manufacturer_similarity, name_tokens, strengths

FALSIFIED_TYPES = {"falsified", "substandard"}
OTHER_BATCHES = "\x00other"  # stands for "the alert lists batches, none of them this holding's"
# Salts, counter-ions and generic words that appear in many unrelated product names.
WEAK_TOKENS = frozenset(
    """
    sodium potassium calcium magnesium zinc iron lithium aluminium aluminum chloride hydrochloride bromide hydrobromide
    acetate sulfate sulphate phosphate citrate bicarbonate carbonate maleate succinate tartrate mesylate besylate besilate
    fumarate nitrate gluconate lactate oxide hydroxide dihydrate monohydrate trihydrate anhydrous water dextrose glucose
    saline vitamin acid base plus extra strength childrens children adult adults infant infants junior original regular
    maximum relief care health healthcare pharma flavor flavour mint cherry orange lemon strawberry grape menthol
    """.split()
)


@dataclass
class AlertFacts:
    name_tokens: set[str]
    product_tokens: set[str]
    lot_keys: set[str]
    all_lots: bool
    manufacturer: str | None
    product_codes: list[str]
    strengths: set[str]
    alert_type: str

    @classmethod
    def from_alert(cls, alert: Alert, lot_keys: set[str] | None = None) -> AlertFacts:
        """Facts about an alert. Pass ``lot_keys`` to avoid loading every batch of a large alert."""
        return cls(
            name_tokens=set(alert.name_tokens or []),
            product_tokens=set(name_tokens(*(alert.product_names or []))),
            lot_keys={lot.lot_key for lot in alert.lots} if lot_keys is None else lot_keys,
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


def _covers(holding_tokens: set[str], alert_tokens: set[str]) -> set[str]:
    """Tokens proving the alert names the holding's product, or an empty set.

    Every distinctive word of the holding's name must appear in the alert, so
    "Levothyroxine Sodium Tablets" needs "levothyroxine". Words such as
    "sodium" or "chloride" only count when the name has nothing more specific.
    """
    required = _strong(holding_tokens) or holding_tokens
    if not required or not required <= alert_tokens:
        return set()
    return required


def _strong(tokens: set[str]) -> set[str]:
    """Distinctive words: four or more letters and not a salt or generic word."""
    return {t for t in tokens if len(t) >= 4 and t not in WEAK_TOKENS}


def _names_product(tokens: set[str]) -> bool:
    """True when matching name words are enough on their own to identify a product.

    That takes one distinctive word, or at least two words. A single short or
    generic word, such as "pan" in "Pan 40", also appears in unrelated names
    ("PAN filter membrane"), so it is not enough without a listed batch.
    """
    return len(tokens) >= 2 or bool(_strong(tokens))


def score(alert: AlertFacts, holding: HoldingFacts, strict: bool = True) -> Decision | None:
    """Decide whether an alert affects a holding, and how confidently.

    Rules (see docs/ARCHITECTURE.md, "Matching algorithm"):

    * A batch-level match needs the batch number **and** product evidence
      (product code, product name or manufacturer), because short batch
      codes collide across unrelated products.
    * A holding whose batch is known and not on the alert's list never matches.
    * A product-level match (the alert covers all batches, lists none, or the
      holding has no batch) needs the alert's *product name* to cover the
      holding's name, no conflicting strength or manufacturer, and positive
      identity evidence: a product code, a matching manufacturer, or, for
      falsified and substandard alerts, the brand name. With ``strict=False``
      (the public batch check) a name match alone is enough.
    * Without a product code, a product-level name match needs one
      distinctive matching word (four or more letters, not a salt or generic
      word), at least two matching words, or a matching manufacturer, so
      "Pan 40" does not match "PAN filter membrane" by name alone.
    """
    reasons: list[str] = []

    code_hit = bool(holding.product_code) and any(codes_match(holding.product_code, c) for c in alert.product_codes)
    name_overlap = _covers(holding.name_tokens, alert.name_tokens)
    product_overlap = _covers(holding.name_tokens, alert.product_tokens)
    maker_known = bool(holding.manufacturer and alert.manufacturer)
    maker_hit = maker_known and manufacturer_similarity(holding.manufacturer, alert.manufacturer) >= 0.5
    maker_conflict = maker_known and not maker_hit
    strength_conflict = bool(holding.strengths and alert.strengths and not holding.strengths & alert.strengths)

    if code_hit:
        reasons.append("Product code matches the alert")
    if name_overlap:
        reasons.append("Product name matches: " + ", ".join(sorted(name_overlap)))
    if maker_hit:
        reasons.append("Manufacturer matches")

    batch_hit = bool(holding.lot_key) and holding.lot_key in alert.lot_keys
    if batch_hit:
        if not (code_hit or name_overlap or maker_hit):
            return None
        confidence = 90 + (5 if code_hit else 0) + (3 if name_overlap else 0) + (2 if maker_hit else 0)
        if strength_conflict:
            confidence -= 15
            reasons.append("Strength differs from the alert: check the pack")
        reasons.insert(0, f"Batch {holding.lot_key} is listed in the alert")
        return Decision("batch", min(confidence, 99), reasons)

    if holding.lot_key and alert.lot_keys and not alert.all_lots:
        return None  # The holding's batch is known and is not affected.
    if strength_conflict or (maker_conflict and not code_hit):
        return None
    if not (code_hit or (product_overlap and (_names_product(product_overlap) or maker_hit))):
        return None
    brand_hit = alert.alert_type in FALSIFIED_TYPES and bool(product_overlap)
    identity = code_hit or maker_hit or brand_hit or (not holding.manufacturer and not alert.manufacturer)
    if strict and not identity:
        return None

    if alert.all_lots:
        reasons.insert(0, "The alert covers all batches of this product")
        confidence = 70
    elif not alert.lot_keys:
        reasons.insert(0, "We could not read batch numbers from this alert: check your batch in the original notice")
        confidence = 62
    else:
        reasons.insert(0, "No batch number was entered: compare the batch on your pack with the batches listed in the alert")
        confidence = 55
    if code_hit:
        confidence += 12
    if maker_hit:
        confidence += 8
    if not identity:
        confidence -= 15
        reasons.append("Same product name, but the manufacturer could not be compared: check the pack")
    return Decision("product", max(30, min(confidence, 89)), reasons)


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
    alerts = session.scalars(
        # Only medicine alerts are matched against workspace stock; food alerts are for consumers.
        select(Alert).where(Alert.id.in_(alert_ids), Alert.category == "medicine").options(selectinload(Alert.lots))
    ).all()
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


def facts_for_holding(alert: Alert, holding_lot_key: str | None, listed: bool) -> AlertFacts:
    """Alert facts sufficient to score one holding, without loading the alert's batch list.

    Scoring only needs to know whether the holding's batch is listed, and
    whether the alert lists any batches at all.
    """
    if listed and holding_lot_key:
        keys = {holding_lot_key}
    elif alert.lot_count:
        keys = {OTHER_BATCHES}
    else:
        keys = set()
    return AlertFacts.from_alert(alert, keys)


def match_holdings(session: Session, holdings: Iterable[Holding]) -> list[uuid.UUID]:
    """Match newly created or edited holdings against recent alerts. Returns new match ids."""
    created: list[uuid.UUID] = []
    cutoff = date.today() - timedelta(days=365 * get_settings().match_lookback_years)
    for holding in holdings:
        if holding.archived_at is not None:
            continue
        listed: set[uuid.UUID] = set()
        conditions = []
        if holding.lot_key:
            listed = set(session.scalars(select(AlertLot.alert_id).where(AlertLot.lot_key == holding.lot_key)))
            if listed:
                conditions.append(Alert.id.in_(listed))
        if holding.name_tokens:
            conditions.append(Alert.name_tokens.overlap(sorted(set(holding.name_tokens))))
        if not conditions:
            continue
        candidates = session.scalars(
            select(Alert).where(
                or_(*conditions),
                or_(Alert.published_at.is_(None), Alert.published_at >= cutoff),
                Alert.category == "medicine",
            )
        ).all()
        facts = HoldingFacts.from_holding(holding)
        for alert in candidates:
            decision = score(facts_for_holding(alert, holding.lot_key, alert.id in listed), facts)
            if decision:
                match_id = _record(session, alert, holding, decision)
                if match_id:
                    created.append(match_id)
    return created


def rescore_open_matches(
    session: Session, holding_ids: Iterable[uuid.UUID] | None = None, alert_ids: Iterable[uuid.UUID] | None = None
) -> tuple[int, int]:
    """Re-apply the current rules to open matches. Returns (updated, removed).

    Used when matching rules change, when a holding is edited (a corrected
    batch number), and when a regulator updates an alert. Open matches that no
    longer qualify are deleted; resolved matches are history and are kept.
    """
    stmt = select(Match).where(Match.status == "open").options(selectinload(Match.alert), selectinload(Match.holding))
    if holding_ids is not None:
        stmt = stmt.where(Match.holding_id.in_(list(holding_ids)))
    if alert_ids is not None:
        stmt = stmt.where(Match.alert_id.in_(list(alert_ids)))
    updated = removed = 0
    for match in session.scalars(stmt).all():
        lot_key = match.holding.lot_key
        listed = (
            bool(lot_key)
            and session.scalar(
                select(func.count()).select_from(AlertLot).where(AlertLot.alert_id == match.alert_id, AlertLot.lot_key == lot_key)
            )
            > 0
        )
        decision = None
        if match.holding.archived_at is None and match.alert.category == "medicine":
            decision = score(facts_for_holding(match.alert, lot_key, listed), HoldingFacts.from_holding(match.holding))
        if decision is None:
            session.delete(match)
            removed += 1
        elif (decision.level, decision.confidence, decision.reasons) != (match.level, match.confidence, match.reasons):
            match.level, match.confidence, match.reasons = decision.level, decision.confidence, decision.reasons
            updated += 1
    session.flush()
    return updated, removed
