import pytest

from batchlight.lots import batch_rows_from_html, extract_lots


def keys(text):
    return extract_lots(text).keys


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Lot: 36-252082, Exp. 7/16/2023; 36-254925, Exp. 7/22/2023.", ["36252082", "36254925"]),
        (
            "Lot #: 1732A, 1732B, 1742A, 1742B, 1742C, 1742D; Exp. 06/2024.",
            ["1732A", "1732B", "1742A", "1742B", "1742C", "1742D"],
        ),
        ("Lot # 20241030, Exp Date: 10/30/2026", ["20241030"]),
        ("Lot# 24ASV002UA, Exp Date: 6/30/2028", ["24ASV002UA"]),
        ("Lot # 1G008A/1G00804722, Exp 07/31/25", ["1G008A1G00804722", "1G008A", "1G00804722"]),
        ("Lot D53797, exp 04/30/27", ["D53797"]),
        ("Lot # NC185424, Exp Date: 2/12/2027; Lot # NC185479, Exp Date: 2/13/2027", ["NC185424", "NC185479"]),
        ("Lot #: 25530722", ["25530722"]),
        ("BNZ-001921, EXP 04/15/2029", ["BNZ001921"]),
        ("Lots: 405-002 and 405-003", ["405002", "405003"]),
        ("Lot: 083L111, Exp. 11/30/2025; 084A032, Exp. 12/31/2025", ["083L111", "084A032"]),
        ("Lot Prorx051425-2, Lot Prorx051425-3 and Lot Prorx051425-4", ["PRORX0514252", "PRORX0514253", "PRORX0514254"]),
        ("Lot H2200493; Exp 6/30/2025", ["H2200493"]),
        ("Lot: BNZ-001646, Exp Date: 11/26/2026, Keystone Item No. 03-43619", ["BNZ001646"]),
        ("Lots VJ8599, Exp Date 09/30/2026 & VL1668, Exp Date 01/31/2027", ["VJ8599", "VL1668"]),
        (
            "Lot #  17241125, exp. date Jun-26 17242202, exp. date  Nov-26 17242204, exp. date Dec-26",
            ["17241125", "17242202", "17242204"],
        ),
        ("Lot #: MN16022, Exp. Date Aug 2027; MN33922, MN31222, Exp. Date Apr 2027", ["MN16022", "MN33922", "MN31222"]),
        (
            "Lot# (a) Lots L300255, L300262, Exp Date 07/31/2025;  (b)L300263, Exp Date 08/31/2025",
            ["L300255", "L300262", "L300263"],
        ),
        ("Lot #: 3047, Exp Date July 2027.", ["3047"]),
        (
            "Lot #: 83449, Exp Date: 10/2026; 82682, Exp Date: 11/2026; 86714, 87302, 87560, Exp Date: 02/2027",
            ["83449", "82682", "86714", "87302", "87560"],
        ),
        (
            "Material Number: M001195290; UDI-DI: 08714729839408; Batch Numbers: 38362172, 38362173, 38530450;",
            ["38362172", "38362173", "38530450"],
        ),
        ("Lot numbers: a) 17230658, Exp.: 02/2025; 17230814,17230822, Exp.: 03/2025", ["17230658", "17230814", "17230822"]),
        ("Beyond Use Date: December 24-29, 2022", []),
        (
            "Telmisartan Lot # 3228113, 3228114, Exp Date: Oct 31, 2026; Lot # 3228120, Exp Date: Nov 30, 2026.",
            ["3228113", "3228114", "3228120"],
        ),
    ],
)
def test_extract_lots_real_fda_formats(text, expected):
    assert keys(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "All lots within expiry.",
        "All lots with expiry through September 2023",
        "All lots within expiry distributed by Gold Star Distribution.",
    ],
)
def test_all_lots_phrases(text):
    result = extract_lots(text)
    assert result.all_lots is True
    assert result.lots == []


def test_expiry_attached_to_clause():
    result = extract_lots("Lot #: 83449, Exp Date: 10/2026; 82682, Exp Date: 11/2026")
    assert [(lot.key, lot.expiry_text) for lot in result.lots] == [("83449", "10/2026"), ("82682", "11/2026")]


def test_multiple_fields_deduplicated():
    result = extract_lots("Lot 12345", "Lot 12345, 67890")
    assert result.keys == ["12345", "67890"]


def test_strengths_and_units_are_not_lots():
    assert keys("Lot 500mg 10ml 0.243% ABC123") == ["ABC123"]


MHRA_TABLE = """
<table><thead><tr><th>Batch No.</th><th>Expiry Date</th><th>Pack Size</th><th>First Distributed</th></tr></thead>
<tbody><tr><td>E941A</td><td>30/04/2027*</td><td>20</td><td>29/10/2025</td></tr>
<tr><td>E941B</td><td>30/04/2027*</td><td>20</td><td>04/02/2026</td></tr></tbody></table>
"""


def test_batch_rows_from_horizontal_table():
    rows = batch_rows_from_html(MHRA_TABLE)
    assert [(r.batch, r.expiry) for r in rows] == [("E941A", "30/04/2027*"), ("E941B", "30/04/2027*")]


def test_batch_rows_multiple_batches_in_cell_and_product_column():
    html = """<table><tr><th>Product name</th><th>Batch number</th><th>Stated manufacturer</th></tr>
    <tr><td>COLDRIF</td><td>SR-13, SR-14</td><td>Sresan Pharmaceutical</td></tr></table>"""
    rows = batch_rows_from_html(html)
    assert [(r.product, r.batch, r.manufacturer) for r in rows] == [
        ("COLDRIF", "SR-13", "Sresan Pharmaceutical"),
        ("COLDRIF", "SR-14", "Sresan Pharmaceutical"),
    ]


def test_batch_rows_vertical_table():
    html = """<table><tr><td>Product name</td><td>DARZALEX</td></tr>
    <tr><td>Batch number</td><td>NFS3S01</td></tr><tr><td>Expiry date</td><td>05/2026</td></tr></table>"""
    rows = batch_rows_from_html(html)
    assert [(r.product, r.batch, r.expiry) for r in rows] == [("DARZALEX", "NFS3S01", "05/2026")]


def test_tables_without_batch_column_are_ignored():
    assert batch_rows_from_html("<table><tr><th>Name</th><th>Role</th></tr><tr><td>A</td><td>B</td></tr></table>") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("t20221117@11 t20221116@36", ["T2022111711", "T2022111636"]),
        ("Lot#: 11182022@2, Exp 1/2/2023", ["111820222"]),
        ("Lot #: F135, Exp. Date March 2025; F146, Exp. Date April , 2025", ["F135", "F146"]),
        ("Lot #: 3H2V, 3H3V, Exp. Date 2026/07; 3J1V, Exp. Date 2026/09", ["3H2V", "3H3V", "3J1V"]),
        ("Lot numbers: a)100 count bottle: RV8686; b) 1000 count bottle: RX0119", ["RV8686", "RX0119"]),
        ("SKUs 0902867 stored and distributed from DCs 07/28/2022 through 03/31/2023.", []),
        ("Lot: 25MAR032, Exp. 07/16/2025.", ["25MAR032"]),
        ("Lot 77A12, Exp 31-Oct-26", ["77A12"]),
    ],
)
def test_extract_lots_edge_formats(text, expected):
    assert keys(text) == expected


def test_health_canada_table_prefers_product_name_and_keeps_cells():
    html = open("tests/fixtures/hc_detail_cyclophosphamide.html", encoding="utf-8").read()
    rows = batch_rows_from_html(html)
    assert len(rows) == 1
    row = rows[0]
    assert (row.batch, row.product, row.manufacturer) == (
        "7Z10346A",
        "Cyclophosphamide for Injection USP",
        "Marcan Pharmaceuticals Inc",
    )
    assert row.cells["market authorization"] == "DIN 02560216"
