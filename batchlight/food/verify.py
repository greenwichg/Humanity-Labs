"""Checks a consumer can run on a pack, using only rules that are published and verifiable.

* Barcodes: the GS1 check digit (GTIN-8, UPC-A/GTIN-12, EAN-13/GTIN-13, GTIN-14).
  A valid check digit only shows the number is well-formed, not that the
  product is genuine: counterfeiters copy real barcodes.
* FSSAI licence and registration numbers: 14 digits. Whether a number is real,
  and whose it is, can only be confirmed on FSSAI's own FoSCoS search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GS1_INDIA_PREFIX = "890"
FOSCOS_URL = "https://foscos.fssai.gov.in/"


def digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def gtin_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for the digits before the check digit."""
    total = 0
    for position, char in enumerate(reversed(body)):
        total += int(char) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10


@dataclass
class BarcodeResult:
    entered: str
    digits: str
    kind: str | None
    valid: bool
    message: str
    gs1_india: bool = False

    @property
    def gtin14(self) -> str | None:
        return self.digits.zfill(14) if self.valid else None


KINDS = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}


def check_barcode(value: str) -> BarcodeResult:
    number = digits(value)
    kind = KINDS.get(len(number))
    if not number:
        return BarcodeResult(value, number, None, False, "Enter the numbers printed under the barcode.")
    if kind is None:
        return BarcodeResult(
            value, number, None, False, f"Barcodes on food packs have 8, 12, 13 or 14 digits. This has {len(number)}."
        )
    expected = gtin_check_digit(number[:-1])
    if int(number[-1]) != expected:
        return BarcodeResult(
            value,
            number,
            kind,
            False,
            "The last digit does not match the barcode's check digit. Check you typed it correctly. "
            "If it is printed this way on the pack, the barcode is not a valid GS1 number.",
        )
    india = kind in ("EAN-13", "GTIN-14") and number.zfill(13)[-13:].startswith(GS1_INDIA_PREFIX)
    return BarcodeResult(value, number, kind, True, "This is a correctly formed barcode number.", india)


def barcode_variants(number: str) -> list[str]:
    """Forms the same GTIN can take in regulator data (with or without leading zeros)."""
    core = number.lstrip("0")
    if not core:
        return []
    return sorted({number, core, core.zfill(12), core.zfill(13), core.zfill(14)})


@dataclass
class LicenceResult:
    entered: str
    digits: str
    well_formed: bool
    message: str


def check_fssai_licence(value: str) -> LicenceResult:
    number = digits(value)
    if not number:
        return LicenceResult(value, number, False, "Enter the 14-digit number printed next to the FSSAI logo.")
    if len(number) != 14:
        return LicenceResult(
            value,
            number,
            False,
            f"FSSAI licence and registration numbers have 14 digits. This has {len(number)}. "
            "A number with the wrong length is a warning sign: report the product if it looks suspicious.",
        )
    return LicenceResult(
        value, number, True, "The number has the right format. Only FSSAI can confirm it is real and whose it is."
    )


_UPC_RE = re.compile(
    r"\b(?:UPC|EAN|GTIN|barcode)(?:\s*(?:code|codes|no\.?|number|#))?\s*[:#]?\s*((?:\d[\s-]?){7,13}\d)", re.IGNORECASE
)


def extract_barcodes(*texts: str | None) -> list[str]:
    """Barcode numbers labelled UPC/EAN/GTIN in free text, kept only when the check digit is valid."""
    found: dict[str, None] = {}
    for text in texts:
        for raw in _UPC_RE.findall(text or ""):
            result = check_barcode(raw)
            if result.valid:
                found.setdefault(result.digits, None)
    return list(found)
