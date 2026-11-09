from batchlight.matching import AlertFacts, HoldingFacts, score
from batchlight.text import name_tokens, strengths


def alert(
    names=("Cinacalcet Tablets",),
    lots=("7717825A",),
    all_lots=False,
    maker="Strides Pharma Science Limited",
    codes=("64380-883",),
    text="Cinacalcet Tablets, 30 mg",
    alert_type="recall",
):
    return AlertFacts(
        name_tokens=set(name_tokens(*names)),
        lot_keys=set(lots),
        all_lots=all_lots,
        manufacturer=maker,
        product_codes=list(codes),
        strengths=strengths(text),
        alert_type=alert_type,
    )


def holding(name="Cinacalcet 30mg", lot="7717825A", maker=None, code=None, strength=None):
    return HoldingFacts(
        name_tokens=set(name_tokens(name)),
        lot_key=lot,
        manufacturer=maker,
        product_code=code,
        strengths=strengths(strength, name),
    )


def test_batch_match_with_name_evidence():
    decision = score(alert(), holding())
    assert decision is not None
    assert decision.level == "batch"
    assert decision.confidence >= 90
    assert decision.reasons[0] == "Batch 7717825A is listed in the alert"


def test_batch_alone_without_product_evidence_does_not_match():
    assert score(alert(), holding(name="Amoxicillin 500mg")) is None


def test_batch_match_via_product_code_only():
    decision = score(alert(), holding(name="Sensipar generic", code="64380-883-04"))
    assert decision and decision.level == "batch" and decision.confidence >= 95


def test_known_batch_not_listed_never_matches():
    assert score(alert(), holding(lot="9999999")) is None


def test_missing_batch_gives_product_level_match():
    decision = score(alert(), holding(lot=None))
    assert decision and decision.level == "product"
    assert "No batch number is recorded" in decision.reasons[0]


def test_all_lots_alert_matches_any_batch_of_same_product():
    decision = score(alert(lots=(), all_lots=True), holding(lot="ANY123", maker="Strides Pharma Inc"))
    assert decision and decision.level == "product" and decision.confidence >= 70


def test_product_level_requires_no_manufacturer_conflict():
    assert score(alert(lots=(), all_lots=True), holding(lot="X1", maker="Cipla Ltd")) is None


def test_strength_conflict_blocks_product_level():
    assert score(alert(lots=(), all_lots=True), holding(name="Cinacalcet 60mg", lot=None)) is None


def test_strength_conflict_lowers_batch_confidence():
    decision = score(alert(), holding(name="Cinacalcet 60mg"))
    assert decision and decision.level == "batch" and decision.confidence < 90


def test_falsified_alert_without_lots_matches_brand_holding():
    falsified = alert(names=("DARZALEX", "daratumumab"), lots=(), maker=None, codes=(), text="DARZALEX", alert_type="falsified")
    decision = score(falsified, holding(name="Darzalex 400mg/20ml", lot="NFS3S01", maker="Janssen"))
    assert decision and decision.level == "product"
    assert "does not list batch numbers" in decision.reasons[0]


def test_unrelated_product_does_not_match():
    assert score(alert(lots=(), all_lots=True), holding(name="Paracetamol 500mg", lot=None)) is None
