"""Infrastructure type from a project's or work's own description. Shown to people as our reading of the text."""

from __future__ import annotations

import re

ASSET_TYPES = {
    "flyover": "Flyover or elevated road",
    "bridge": "Bridge, culvert or tunnel",
    "highway": "Highway or expressway",
    "road": "Road",
    "dam": "Dam or barrage",
    "irrigation": "Irrigation or canal",
    "drainage": "Drainage or flood control",
    "sanitation": "Sewerage or sanitation",
    "water_supply": "Water supply",
    "building": "Public building",
    "railway": "Railway or metro",
    "power": "Power",
    "urban": "Urban infrastructure",
    "other": "Other public works",
}

# Most specific first: "road over bridge" is a bridge, "canal road" is irrigation only if nothing more specific matches.
_RULES: list[tuple[str, str]] = [
    ("flyover", r"flyover|fly over|overpass|elevated (?:road|corridor)|grade separator"),
    ("bridge", r"bridges?|\brob\b|\brub\b|culverts?|tunnels?|underpass|causeway"),
    ("dam", r"\bdams?\b|barrage|reservoir|spillway|weir"),
    ("drainage", r"drains?\b|drainage|storm ?water|nallah?|nala\b|flood (?:protection|control|wall)|embankment"),
    ("sanitation", r"sewer|sewage|sewerage|\bstp\b|sanitation|toilets?|septage|faecal|solid waste"),
    ("water_supply", r"water supply|drinking water|water treatment|\bwtp\b|pipeline|overhead tank|\boht\b|water distribution"),
    ("irrigation", r"irrigation|canals?\b|minor irrigation|lift scheme|watershed"),
    ("highway", r"highways?|expressway|\bnh[- ]?\d|national highway|state highway|\bsh[- ]?\d"),
    ("road", r"\broads?\b|pavement|carriageway|bituminous|resurfacing|pmgsy|footpath|street"),
    ("railway", r"railway|\brail\b|metro|station building"),
    (
        "building",
        r"building|hospital|school|college|hostel|office|bhawan|bhavan|anganwadi|shelter|warehouse|market|auditorium|quarters",
    ),
    ("power", r"substation|transmission line|power|solar|electrification|hydro ?electric"),
    ("urban", r"urban|smart city|municipal|city resilience"),
]
_COMPILED = [(kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _RULES]


def classify(*texts: str | None) -> str:
    text = " ".join(t for t in texts if t)
    for kind, pattern in _COMPILED:
        if pattern.search(text):
            return kind
    return "other"
