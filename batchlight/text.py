"""Normalisation helpers shared by ingestion, matching and the UI."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

_LOT_LABEL_RE = re.compile(
    r"^(?:BATCH\s?NUMBERS?|LOT\s?NUMBERS?|BATCH\s?NO\.?|LOT\s?NO\.?|B\.?\s?NO\.?|BATCH(?:ES)?|LOTS?)(?=[\s#:.\-])[\s#:.\-]*",
    re.IGNORECASE,
)


def ascii_fold(value: str) -> str:
    """Strip accents and normalise unicode compatibility characters."""
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def lot_key(raw: str | None) -> str:
    """Normalise a batch or lot code for comparison.

    Uppercases, strips a leading "Lot"/"Batch" label and removes every
    character that is not a letter or digit, so ``"Lot # ab-123/4"``
    becomes ``"AB1234"``.
    """
    if not raw:
        return ""
    value = ascii_fold(raw).upper().strip()
    value = _LOT_LABEL_RE.sub("", value)
    return re.sub(r"[^A-Z0-9]", "", value)


# Words that describe dosage form, packaging or regulatory boilerplate rather
# than the identity of the medicine. Removing them makes "Valsartan Tablets USP
# 160 mg" and "valsartan 160mg" compare as the same product.
_NAME_STOPWORDS = frozenset(
    """
    a an and as at by each for from in into of on or per the to with without
    tablet tablets tab tabs capsule capsules cap caps caplet caplets injection injections injectable inj
    solution solutions suspension suspensions oral syrup syrups elixir cream creams ointment ointments gel gels
    lotion drops drop eye eyes ear ears nasal spray sprays powder powders granules sachet sachets patch patches
    inhaler inhalation aerosol suppository suppositories emulsion concentrate infusion intravenous topical
    film coated uncoated chewable dispersible effervescent extended delayed modified prolonged release
    sustained controlled immediate er xr xl sr cr dr ir la mr odt
    usp bp ip ph eur nf jp rx only otc single multi dose doses dosage vial vials ampoule ampoules ampule
    bottle bottles count ct pack packs carton cartons box boxes blister blisters strip strips tube tubes
    bag bags container containers unit units syringe syringes prefilled pre filled kit kits
    mg mcg ug g kg ml l iu meq mmol oz net wt weight
    hcl hydrochloride
    manufactured manufacturer distributed distributor marketed packaged repackaged relabeled made
    ndc upc lot lots batch batches exp expiry item code product products contains each
    inc llc ltd limited corp corporation co company pvt private plc gmbh
    sterile preservative free generic brand new
    """.split()
)

_UNIT_TOKEN_RE = re.compile(r"^\d+(?:[.,]\d+)?(?:mg|mcg|ug|g|kg|ml|l|iu|meq|mmol|oz|units?|%)?$")


def name_tokens(*parts: str | None) -> list[str]:
    """Significant lowercase tokens identifying a product, de-duplicated in order."""
    seen: dict[str, None] = {}
    for part in parts:
        if not part:
            continue
        for token in re.split(r"[^a-z0-9]+", ascii_fold(part).lower()):
            if len(token) < 3 or token in _NAME_STOPWORDS or _UNIT_TOKEN_RE.match(token):
                continue
            if token.isdigit():
                continue
            seen.setdefault(token, None)
    return list(seen)


_STRENGTH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mg|mcg|µg|ug|g|ml|iu|units?|%)(?![a-z])", re.IGNORECASE)


def strengths(*parts: str | None) -> set[str]:
    """Numeric strengths such as ``{"30mg", "2.5ml"}`` found in the text."""
    found: set[str] = set()
    for part in parts:
        if not part:
            continue
        for number, unit in _STRENGTH_RE.findall(part):
            unit = unit.lower().replace("µg", "mcg").replace("ug", "mcg")
            if unit.startswith("unit"):
                unit = "units"
            number = number.replace(",", ".")
            if "." in number:
                number = number.rstrip("0").rstrip(".")
            found.add(f"{number}{unit}")
    return found


_CORPORATE_SUFFIXES = frozenset(
    """
    inc incorporated llc l.l.c ltd limited plc pvt private co company corp corporation gmbh ag sa sas srl spa
    bv nv kg kgaa pty lp llp the usa us group holdings holding international intl
    """.split()
)
_PHARMA_WORDS = {
    "pharmaceuticals": "pharma",
    "pharmaceutical": "pharma",
    "pharmaceutica": "pharma",
    "pharmaceutique": "pharma",
    "pharm": "pharma",
    "pharmas": "pharma",
    "laboratories": "labs",
    "laboratory": "labs",
    "lab": "labs",
    "laboratorios": "labs",
}
_GENERIC_COMPANY_WORDS = frozenset(
    {
        "pharma",
        "labs",
        "healthcare",
        "health",
        "medical",
        "life",
        "sciences",
        "science",
        "industries",
        "industry",
        "products",
        "biotech",
        "drugs",
        "remedies",
    }
)


def manufacturer_tokens(name: str | None) -> list[str]:
    tokens = []
    for token in re.split(r"[^a-z0-9]+", ascii_fold(name or "").lower()):
        if not token or token in _CORPORATE_SUFFIXES:
            continue
        tokens.append(_PHARMA_WORDS.get(token, token))
    return tokens


def manufacturer_key(name: str | None) -> str:
    """Stable key for grouping alerts by manufacturer, e.g. ``"strides pharma science"``."""
    return " ".join(manufacturer_tokens(name))


def manufacturer_slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def manufacturer_similarity(a: str | None, b: str | None) -> float:
    """Jaccard similarity of distinctive company-name tokens (0..1)."""
    ta, tb = set(manufacturer_tokens(a)), set(manufacturer_tokens(b))
    da, db = ta - _GENERIC_COMPANY_WORDS, tb - _GENERIC_COMPANY_WORDS
    if da and db:
        ta, tb = da, db
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def code_key(raw: str | None) -> str:
    """Digits-only form of a product code (NDC, GTIN, UDI-DI)."""
    return re.sub(r"\D", "", raw or "")


def codes_match(a: str, b: str) -> bool:
    """True when two product codes refer to the same product.

    Package-level codes extend product-level codes (an NDC product code
    ``42023-182`` prefixes the package code ``42023-182-01``), so a prefix
    match of at least seven digits counts.
    """
    ka, kb = code_key(a), code_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 7 and long_.startswith(short)


_NDC_RE = re.compile(r"\b(\d{4,5}-\d{3,4}(?:-\d{1,2})?)\b")
_GTIN_RE = re.compile(r"\b(?:UDI(?:-DI)?|GTIN|DI)\s*[:#]?\s*(?:\(01\)\s*)?(\d{12,14})\b", re.IGNORECASE)


def extract_product_codes(*parts: str | None) -> list[str]:
    """NDC, GTIN and UDI-DI codes mentioned in free text, as product-level codes."""
    out: dict[str, None] = {}
    for part in parts:
        if not part:
            continue
        for ndc in _NDC_RE.findall(part):
            segments = ndc.split("-")
            out.setdefault("-".join(segments[:2]), None)
        for gtin in _GTIN_RE.findall(part):
            out.setdefault(gtin, None)
    return list(out)


def truncate(value: str | None, length: int) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= length else value[: length - 1].rstrip() + "…"


# Government, parliamentary and intergovernmental domains. A link anywhere else is a news, NGO or other report.
OFFICIAL_DOMAINS = (
    "gov",
    "gov.in",
    "nic.in",
    "sansad.in",
    "gc.ca",
    "canada.ca",
    "gov.uk",
    "gov.hk",
    "gov.sg",
    "who.int",
    "europa.eu",
)


def source_kind(url: str | None) -> str:
    """Return "official" for a government, Parliament or WHO link, otherwise "report" (news, NGO or another publisher)."""
    host = (urlparse(url or "").hostname or "").lower()
    return "official" if any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS) else "report"


def source_host(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host.removeprefix("www.")
