"""India's 28 states and 8 union territories, and finding them in official text.

Slugs are our own stable identifiers (used in URLs). Matching is on whole words only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    slug: str
    name: str
    kind: str  # "state" or "union territory"
    aliases: tuple[str, ...] = ()


REGIONS: tuple[Region, ...] = (
    Region("andhra-pradesh", "Andhra Pradesh", "state"),
    Region("arunachal-pradesh", "Arunachal Pradesh", "state"),
    Region("assam", "Assam", "state"),
    Region("bihar", "Bihar", "state"),
    Region("chhattisgarh", "Chhattisgarh", "state", ("chattisgarh",)),
    Region("goa", "Goa", "state"),
    Region("gujarat", "Gujarat", "state"),
    Region("haryana", "Haryana", "state"),
    Region("himachal-pradesh", "Himachal Pradesh", "state"),
    Region("jharkhand", "Jharkhand", "state"),
    Region("karnataka", "Karnataka", "state"),
    Region("kerala", "Kerala", "state"),
    Region("madhya-pradesh", "Madhya Pradesh", "state"),
    Region("maharashtra", "Maharashtra", "state", ("maharasthra",)),
    Region("manipur", "Manipur", "state"),
    Region("meghalaya", "Meghalaya", "state"),
    Region("mizoram", "Mizoram", "state"),
    Region("nagaland", "Nagaland", "state"),
    Region("odisha", "Odisha", "state", ("orissa",)),
    Region("punjab", "Punjab", "state"),
    Region("rajasthan", "Rajasthan", "state"),
    Region("sikkim", "Sikkim", "state"),
    Region("tamil-nadu", "Tamil Nadu", "state", ("tamilnadu",)),
    Region("telangana", "Telangana", "state"),
    Region("tripura", "Tripura", "state"),
    Region("uttar-pradesh", "Uttar Pradesh", "state"),
    Region("uttarakhand", "Uttarakhand", "state", ("uttaranchal",)),
    Region("west-bengal", "West Bengal", "state"),
    Region("andaman-and-nicobar-islands", "Andaman and Nicobar Islands", "union territory", ("andaman",)),
    Region("chandigarh", "Chandigarh", "union territory"),
    Region(
        "dadra-nagar-haveli-daman-diu",
        "Dadra and Nagar Haveli and Daman and Diu",
        "union territory",
        ("dadra and nagar haveli", "daman and diu"),
    ),
    Region("delhi", "Delhi", "union territory", ("new delhi", "nct of delhi")),
    Region("jammu-and-kashmir", "Jammu and Kashmir", "union territory", ("jammu & kashmir", "j&k")),
    Region("ladakh", "Ladakh", "union territory"),
    Region("lakshadweep", "Lakshadweep", "union territory"),
    Region("puducherry", "Puducherry", "union territory", ("pondicherry",)),
)
BY_SLUG = {r.slug: r for r in REGIONS}

_PATTERNS = [
    (r.slug, re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(n) for n in (r.name.lower(), *r.aliases)) + r")(?![a-z0-9])"))
    for r in REGIONS
]


def find_regions(*texts: str | None) -> list[str]:
    """Slugs of every state or union territory named in the texts, in the order of REGIONS."""
    text = " ".join(t for t in texts if t).lower().replace("_", " ")
    if not text:
        return []
    return [slug for slug, pattern in _PATTERNS if pattern.search(text)]


def region_name(slug: str) -> str:
    region = BY_SLUG.get(slug)
    return region.name if region else slug
