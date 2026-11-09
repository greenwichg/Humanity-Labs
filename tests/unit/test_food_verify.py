import pytest

from batchlight.food.verify import barcode_variants, check_barcode, check_fssai_licence, extract_barcodes, gtin_check_digit


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
    assert check_fssai_licence("1001 2345 6789 01").well_formed is True
    bad = check_fssai_licence("1234567")
    assert bad.well_formed is False and "14 digits" in bad.message
    assert check_fssai_licence("").well_formed is False


def test_extract_barcodes_keeps_only_valid_numbers():
    text = "Product X, 16 oz, UPC 0 36000 29145 2; also UPC: 036000291453 (typo); EAN 4006381333931"
    assert extract_barcodes(text) == ["036000291452", "4006381333931"]
