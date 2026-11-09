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
        product_tokens=set(name_tokens(*names)),
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
    decision = score(alert(), holding(lot=None, maker="Strides Pharma Inc"))
    assert decision and decision.level == "product"
    assert "No batch number is recorded" in decision.reasons[0]


def test_all_lots_alert_matches_any_batch_of_same_product_from_same_maker():
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


def test_salt_words_alone_are_not_product_evidence():
    epinephrine = alert(
        names=("EPINEPHRINE, SODIUM BICARBONATE & DEXTROSE INJECTIONS",), lots=(), all_lots=True, maker=None, codes=(), text=""
    )
    assert score(epinephrine, holding(name="Levothyroxine Sodium Tablets 50 mcg", lot="D2402551")) is None
    assert score(epinephrine, holding(name="Levothyroxine Sodium Tablets 50 mcg", lot=None), strict=False) is None


def test_name_with_only_weak_words_needs_all_of_them():
    saline = alert(names=("0.9% Sodium Chloride Injection",), lots=(), all_lots=True, maker=None, codes=(), text="0.9%")
    bicarbonate = alert(names=("Sodium Bicarbonate Injection",), lots=(), all_lots=True, maker=None, codes=(), text="")
    assert score(saline, holding(name="Sodium Chloride 0.9%", lot=None)) is not None
    assert score(bicarbonate, holding(name="Sodium Chloride 0.9%", lot=None)) is None


def test_product_level_needs_identity_evidence_when_alert_names_a_maker():
    recall = alert(lots=(), all_lots=True)  # maker: Strides
    assert score(recall, holding(lot=None)) is None  # holding maker unknown: not raised in stock matching
    loose = score(recall, holding(lot=None), strict=False)  # but the public check still shows it, flagged
    assert loose and loose.level == "product" and "could not be compared" in loose.reasons[-1]
    assert score(recall, holding(lot=None, maker="Strides Pharma Inc")) is not None
    assert score(recall, holding(lot=None, code="64380-883-04")) is not None


def test_ingredient_alone_does_not_match_a_branded_product_alert():
    coldrif = AlertFacts(
        name_tokens={"coldrif", "paracetamol", "phenylephrine"},
        product_tokens={"coldrif"},
        lot_keys={"SR13"},
        all_lots=False,
        manufacturer="Sresan Pharmaceutical",
        product_codes=[],
        strengths=set(),
        alert_type="substandard",
    )
    assert score(coldrif, holding(name="Paracetamol 500mg", lot=None)) is None
    assert score(coldrif, holding(name="Coldrif syrup", lot=None)) is not None  # brand match on a substandard alert
    healmoxy = AlertFacts(
        name_tokens={"healmoxy", "amoxicillin"},
        product_tokens={"healmoxy"},
        lot_keys=set(),
        all_lots=False,
        manufacturer=None,
        product_codes=[],
        strengths=set(),
        alert_type="falsified",
    )
    assert score(healmoxy, holding(name="Amoxicillin 500 mg capsules", lot=None)) is None
