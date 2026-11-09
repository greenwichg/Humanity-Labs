from batchlight.text import (
    codes_match,
    extract_product_codes,
    lot_key,
    manufacturer_key,
    manufacturer_similarity,
    name_tokens,
    strengths,
)


def test_lot_key_normalises_labels_and_separators():
    assert lot_key("Lot # ab-123/4") == "AB1234"
    assert lot_key("Batch No. SR 13") == "SR13"
    assert lot_key("B.No: 2024-07A") == "202407A"
    assert lot_key("LOT123") == "LOT123"  # a code that merely starts with LOT is kept
    assert lot_key(None) == ""


def test_name_tokens_drop_forms_units_and_boilerplate():
    assert name_tokens("Valsartan Tablets USP, 160 mg") == ["valsartan"]
    assert name_tokens("Metformin HCl ER 500mg", "metformin") == ["metformin"]
    assert name_tokens("Argatroban Injection, USP, 250 mg/2.5mL") == ["argatroban"]


def test_strengths():
    assert strengths("Argatroban 250 mg/2.5mL (100 mg/mL)") == {"250mg", "2.5ml", "100mg"}
    assert strengths("sodium fluoride 0.243%") == {"0.243%"}
    assert strengths("40mg/5 mg") == {"40mg", "5mg"}


def test_manufacturer_key_and_similarity():
    assert manufacturer_key("Strides Pharma Science Ltd.") == "strides pharma science"
    assert manufacturer_key("Hikma Pharmaceuticals USA Inc.") == "hikma pharma"
    assert manufacturer_similarity("Strides Pharma Science Limited", "STRIDES PHARMA INC") >= 0.5
    assert manufacturer_similarity("Cipla Ltd", "Strides Pharma Inc") == 0.0


def test_codes_match_prefix_and_exact():
    assert codes_match("64380-883", "64380-883-04")
    assert codes_match("08714729839408", "08714729839408")
    assert not codes_match("64380-883", "64380-884-04")
    assert not codes_match("123", "1234567")


def test_extract_product_codes():
    text = "Cinacalcet Tablets, 30 mg, NDC 64380-883-04; UDI-DI: 08714729839408"
    assert extract_product_codes(text) == ["64380-883", "08714729839408"]


def test_holding_input_blank_optional_fields():
    from batchlight.services.holdings import HoldingInput

    data = HoldingInput(product_name="Coldrif syrup", lot="SR 13", quantity="", expiry_date="", product_code="", notes=" ")
    assert (data.quantity, data.expiry_date, data.product_code, data.notes) == (None, None, None, None)
    assert HoldingInput(product_name="X item", quantity="1,200").quantity == 1200
