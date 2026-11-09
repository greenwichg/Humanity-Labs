from batchlight.text import (
    codes_match,
    extract_product_codes,
    lot_key,
    manufacturer_key,
    manufacturer_similarity,
    name_tokens,
    source_host,
    source_kind,
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


def test_source_kind_official_hosts():
    for url in [
        "https://sansad.in/getFile/loksabhaquestions/annex/186/AU1094_lBx885.pdf?source=pqals",
        "https://www.cfs.gov.hk/english/press/20240405_10919.html",
        "https://www.who.int/news/item/05-10-2022-medical-product-alert-n-6-2022",
        "https://cdn.who.int/media/docs/default-source/substandard-and-falsified/n5_2025_syrup-medicines_en.pdf",
        "https://www.fssai.gov.in/docs/latest/press/Revised%20Press%20Release%20Gujarat%20Enforcement.pdf",
        "https://cdsco.gov.in/",
        "https://api.fda.gov/drug/enforcement.json",
    ]:
        assert source_kind(url) == "official", url


def test_source_kind_news_and_lookalike_hosts():
    for url in [
        "https://www.businesstoday.in/india/story/example-559413-2026-10-04",
        "https://medicaldialogues.in/news/industry/pharma/example-156618",
        "https://www.business-standard.com/india-news/example.html",
        "https://who.int.example.com/fake",  # a look-alike domain is not WHO
        "https://notgov.in/",
        "http://",
        "",
        None,
    ]:
        assert source_kind(url) == "report", url


def test_source_host():
    assert source_host("https://www.businesstoday.in/india/story/x") == "businesstoday.in"
    assert source_host("https://cdn.who.int/media/docs/x.pdf") == "cdn.who.int"
    assert source_host("https://WWW.CFS.GOV.HK/english/press/x.html") == "cfs.gov.hk"
    assert source_host("https://sansad.in/getFile/x.pdf?source=pqals") == "sansad.in"
    assert source_host(None) == "" and source_host("not a url") == ""
