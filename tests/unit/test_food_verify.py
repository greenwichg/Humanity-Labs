import pytest

from batchlight.food.verify import (
    UPC_FIRST,
    barcode_variants,
    check_barcode,
    check_fssai_licence,
    extract_barcodes,
    gtin_check_digit,
    gtins_in_text,
    upce_to_upca,
)


@pytest.mark.parametrize(
    ("number", "kind"),
    [
        ("4006381333931", "EAN-13"),  # GS1's published EAN-13 example
        ("036000291452", "UPC-A"),  # widely published UPC-A example
        ("96385074", "EAN-8"),
        ("6926104942661", "EAN-13"),  # UPC printed in a CFIA recall notice
        ("8901058000290", "EAN-13"),  # GS1 India prefix 890
    ],
)
def test_valid_barcodes(number, kind):
    result = check_barcode(number)
    assert result.valid and result.kind == kind


def test_check_digit_and_india_prefix():
    assert gtin_check_digit("400638133393") == 1
    assert check_barcode("8901058000290").gs1_india is True
    assert check_barcode("4006381333931").gs1_india is False


def test_invalid_barcodes_explain_why():
    assert "check digit" in check_barcode("4006381333932").message
    assert "8, 12, 13 or 14 digits" in check_barcode("12345").message
    assert check_barcode("").valid is False
    assert check_barcode("6 926104 942661").valid is True  # spaces as printed


def test_barcode_variants_cover_leading_zero_forms():
    variants = barcode_variants("036000291452")
    assert {"36000291452", "036000291452", "0036000291452", "00036000291452"} <= set(variants)


def test_fssai_licence_format():
    good = check_fssai_licence("1001 2345 6789 01")
    assert good.well_formed is True
    # A well-formed number is not reassurance: only FSSAI's own search shows whose licence it is.
    assert "That does not show it is real" in good.message and "FoSCoS" in good.message
    bad = check_fssai_licence("1234567")
    assert bad.well_formed is False and "14 digits" in bad.message and "Check you copied all 14 digits" in bad.message
    assert "warning sign" not in bad.message
    assert check_fssai_licence("").well_formed is False


def test_extract_barcodes_keeps_only_valid_numbers():
    text = "Product X, 16 oz, UPC 0 36000 29145 2; also UPC: 036000291453 (typo); EAN 4006381333931"
    assert extract_barcodes(text) == ["036000291452", "4006381333931"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # as printed in openFDA food enforcement records
        ("UPC 7 41643 05576 6, 9 retail units per case", ["741643055766"]),
        ("UPC: 687456214160 3.", ["687456214160"]),
        ("Net Wt. 16 oz (454 g), lot 2024-118, best by 12/2026", []),
    ],
)
def test_extract_barcodes_from_regulator_text(text, expected):
    assert extract_barcodes(text) == expected


def test_upc_e_codes_expand_to_the_upc_a_regulators_publish():
    assert upce_to_upca("04963406") == "049000006346"
    result = check_barcode("04963406")
    assert result.valid and result.kind == "UPC-E"
    assert result.search_digits == "049000006346" and result.gtin14 == "00049000006346"


def test_cfia_upc_cells_with_several_codes():
    assert gtins_in_text("6 926104 942661 0 12345 67890 5", UPC_FIRST) == ["6926104942661", "012345678905"]
    assert gtins_in_text("", UPC_FIRST) == []
