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
    expanded: str | None = None  # UPC-A form of a UPC-E code

    @property
    def search_digits(self) -> str:
        """The form regulators publish: UPC-E codes are listed as their UPC-A expansion."""
        return self.expanded or self.digits

    @property
    def gtin14(self) -> str | None:
        return self.search_digits.zfill(14) if self.valid else None


KINDS = {8: "EAN-8", 12: "UPC-A", 13: "EAN-13", 14: "GTIN-14"}


def _valid(number: str) -> bool:
    return len(number) in KINDS and int(number[-1]) == gtin_check_digit(number[:-1])


def upce_to_upca(code: str) -> str | None:
    """Expand an 8-digit UPC-E code (number system 0 or 1) to UPC-A, or None if it is not valid UPC-E."""
    if len(code) != 8 or not code.isdigit() or code[0] not in "01":
        return None
    system, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    upca = system + body + check
    return upca if _valid(upca) else None


def check_barcode(value: str) -> BarcodeResult:
    number = digits(value)
    kind = KINDS.get(len(number))
    if not number:
        return BarcodeResult(value, number, None, False, "Enter the numbers printed under the barcode.")
    if kind is None:
        return BarcodeResult(
            value, number, None, False, f"Barcodes on food packs have 8, 12, 13 or 14 digits. This has {len(number)}."
        )
    if not _valid(number):
        expanded = upce_to_upca(number) if len(number) == 8 else None
        if expanded:
            return BarcodeResult(value, number, "UPC-E", True, "This is a correctly formed barcode number.", False, expanded)
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
    """Forms the same GTIN can take in regulator data (with or without leading zeros, UPC-E expanded)."""
    forms = {number}
    if len(number) == 8 and not _valid(number):
        expanded = upce_to_upca(number)
        if expanded:
            forms.add(expanded)
    out: set[str] = set()
    for form in forms:
        core = form.lstrip("0")
        if core:
            out |= {form, core, core.zfill(12), core.zfill(13), core.zfill(14)}
    return sorted(out)


UPC_FIRST = (12, 13, 14, 8)
EAN_FIRST = (13, 14, 12, 8)


def gtins_in_text(text: str | None, prefer: tuple[int, ...] = UPC_FIRST) -> list[str]:
    """Valid barcodes in a run of digit groups such as "7 41643 05576 6, 0 12345 67890 5".

    Commas, semicolons, slashes and the words "and"/"or" separate codes. Within
    a code, digit groups are joined only into a length whose check digit is
    valid, preferring the lengths in ``prefer``, so a trailing pack size or
    count is not swallowed into the barcode.
    """
    found: list[str] = []
    for segment in re.split(r"[,;/&]|\b(?:and|or)\b", text or "", flags=re.IGNORECASE):
        groups = re.findall(r"\d+", segment)
        i = 0
        while i < len(groups):
            candidates: dict[int, tuple[str, int]] = {}
            joined = ""
            for j in range(i, len(groups)):
                joined += groups[j]
                if len(joined) > 14:
                    break
                result = check_barcode(joined)
                if result.valid:
                    candidates.setdefault(len(joined), (result.search_digits, j))
            choice = next((candidates[n] for n in prefer if n in candidates), None)
            if choice:
                if choice[0] not in found:
                    found.append(choice[0])
                i = choice[1] + 1
            else:
                i += 1
    return found


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
            "Check you copied all 14 digits. If the number printed on the pack really has a different length, "
            "that is worth reporting.",
        )
    return LicenceResult(
        value,
        number,
        True,
        "The number has the right format. That does not show it is real: only FSSAI's FoSCoS search can show whose "
        "licence it is.",
    )


_LABEL_RE = re.compile(
    r"\b(UPC|EAN|GTIN|barcode)s?(?:\s*(?:code|codes|no\.?|number|numbers|#))?\s*(?:\([^)]{0,40}\))?\s*[:#]?",
    re.IGNORECASE,
)
_RUN_RE = re.compile(r"(?:[\s\t]*(?:\d+|[,;/&-]|\band\b|\bor\b))+", re.IGNORECASE)


def extract_barcodes(*texts: str | None) -> list[str]:
    """Barcode numbers labelled UPC/EAN/GTIN in free text, kept only when the check digit is valid."""
    found: dict[str, None] = {}
    for text in texts:
        text = text or ""
        for label in _LABEL_RE.finditer(text):
            run = _RUN_RE.match(text, label.end())
            if not run:
                continue
            prefer = UPC_FIRST if label.group(1).upper() == "UPC" else EAN_FIRST
            for code in gtins_in_text(run.group(0)[:400], prefer):
                found.setdefault(code, None)
    return list(found)
