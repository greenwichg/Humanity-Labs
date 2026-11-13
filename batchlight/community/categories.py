"""Infrastructure categories, each with its own visual prompts, defect queries and scoring criteria.

Everything the assessment engine looks for is listed here, so the criteria shown to people are the criteria used.
Scores describe visible condition only. They are never combined across categories.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Defect:
    key: str
    label: str  # shown to people
    query: str  # open-vocabulary detection query (OWLv2)
    weight: float  # share of the "visible defects" criterion this defect can take


@dataclass(frozen=True)
class Category:
    slug: str
    label: str
    examples: str
    scene_prompts: tuple[str, ...]  # CLIP: does the photo show this kind of infrastructure?
    condition_prompts: tuple[str, str, str]  # CLIP: good, fair, poor visible condition
    defects: tuple[Defect, ...]
    damage_prompts: tuple[str, ...]  # CLIPSeg: regions that look damaged, for the damaged-area measure
    infra_types: tuple[str, ...]  # matching asset types in Infrastructure Transparency
    # Maximum points each criterion can remove from 100. Detected defects are shown but carry no points: in our test
    # (docs/COMMUNITY.md) the detector fired about as often on ordinary street photos as on pothole photos.
    weights: dict[str, float] = field(default_factory=lambda: {"condition": 70.0, "defects": 0.0, "area": 30.0})


def _d(key: str, label: str, query: str, weight: float) -> Defect:
    return Defect(key, label, query, weight)


CATEGORIES: tuple[Category, ...] = (
    Category(
        "roads",
        "Roads",
        "Village, town and city roads, streets and lanes",
        (
            "a photo of a road",
            "a photo of a street with buildings",
            "a photo of a village road",
            "a photo of a narrow lane",
            "a close-up photo of a damaged road surface",
            "a photo of a pothole in a road",
        ),
        (
            "a photo of a smooth, well maintained road",
            "a photo of a road with some cracks and patches",
            "a photo of a badly damaged road full of potholes",
        ),
        (
            _d("pothole", "Pothole", "a pothole in the road", 0.45),
            _d("crack", "Crack in the surface", "a crack in the road surface", 0.25),
            _d("broken_edge", "Broken road edge", "a broken, crumbling road edge", 0.15),
            _d("waterlogging", "Standing water", "a puddle of standing water on the road", 0.15),
        ),
        ("pothole", "cracked broken road surface", "muddy water on road"),
        ("road",),
    ),
    Category(
        "highways",
        "Highways and expressways",
        "National and state highways, expressways and their service roads",
        ("a photo of a highway", "a photo of a multi-lane expressway", "a photo of a national highway with a median"),
        (
            "a photo of a smooth, well maintained highway",
            "a photo of a highway with some cracks and patches",
            "a photo of a badly damaged highway with potholes",
        ),
        (
            _d("pothole", "Pothole", "a pothole in the road", 0.4),
            _d("crack", "Crack in the surface", "a crack in the road surface", 0.2),
            _d("barrier", "Damaged crash barrier", "a damaged, bent crash barrier", 0.2),
            _d("signage", "Damaged or missing sign", "a damaged road sign", 0.2),
        ),
        ("pothole", "cracked broken road surface", "bent damaged metal barrier"),
        ("highway",),
    ),
    Category(
        "flyovers",
        "Flyovers",
        "Flyovers, overpasses and elevated roads",
        ("a photo of a flyover", "a photo of an elevated road on pillars", "a photo of an overpass above a road"),
        (
            "a photo of a clean, well maintained flyover",
            "a photo of a flyover with stains and minor damage",
            "a photo of a flyover with broken concrete and exposed steel",
        ),
        (
            _d("spalling", "Broken or flaking concrete", "broken flaking concrete with exposed steel bars", 0.35),
            _d("crack", "Crack in concrete", "a crack in a concrete wall", 0.25),
            _d("railing", "Damaged railing", "a broken railing", 0.2),
            _d("pothole", "Pothole on the deck", "a pothole in the road", 0.2),
        ),
        ("broken concrete with exposed rusty steel", "cracked concrete", "pothole"),
        ("flyover",),
    ),
    Category(
        "bridges",
        "Bridges",
        "Bridges, culverts, footbridges and causeways",
        ("a photo of a bridge over a river", "a photo of a concrete bridge", "a photo of a footbridge", "a photo of a culvert"),
        (
            "a photo of a clean, well maintained bridge",
            "a photo of a bridge with stains, rust and minor damage",
            "a photo of a bridge with broken concrete, exposed steel and broken railings",
        ),
        (
            _d("spalling", "Broken or flaking concrete", "broken flaking concrete with exposed steel bars", 0.3),
            _d("crack", "Crack in concrete", "a crack in a concrete wall", 0.25),
            _d("railing", "Damaged or missing railing", "a broken railing", 0.25),
            _d("rust", "Rust on steel", "rusty corroded steel", 0.2),
        ),
        ("broken concrete with exposed rusty steel", "cracked concrete", "rust"),
        ("bridge",),
    ),
    Category(
        "dams",
        "Dams",
        "Dams, barrages, weirs and their walls, gates and embankments",
        (
            "a photo of a dam",
            "a photo of a dam wall with water",
            "a photo of a barrage with gates",
            "a photo of a reservoir embankment",
        ),
        (
            "a photo of a clean, well maintained dam",
            "a photo of a dam with stains, weeds and minor wear",
            "a photo of a dam with broken concrete and heavy vegetation on the walls",
        ),
        (
            _d("crack", "Visible crack in a wall", "a crack in a concrete wall", 0.3),
            _d("spalling", "Broken or flaking concrete", "broken flaking concrete", 0.25),
            _d("vegetation", "Plants growing on the structure", "plants and weeds growing on a concrete wall", 0.25),
            _d("rust", "Rust on gates or steelwork", "rusty corroded steel", 0.2),
        ),
        ("cracked concrete", "weeds growing on concrete", "rust"),
        ("dam", "irrigation"),
    ),
    Category(
        "schools",
        "Government schools",
        "Government school buildings, classrooms, toilets and boundary walls",
        ("a photo of a school building", "a photo of a classroom", "a photo of a government school in India"),
        (
            "a photo of a clean, well maintained school building",
            "a photo of a school building with peeling paint and minor damage",
            "a photo of a run-down school building with broken windows and a damaged roof",
        ),
        (
            _d("window", "Broken window or door", "a broken window", 0.25),
            _d("damp", "Damp, peeling or stained wall", "a damp wall with peeling paint", 0.25),
            _d("roof", "Damaged roof or ceiling", "a damaged roof", 0.3),
            _d("wall", "Broken boundary wall", "a broken wall", 0.2),
        ),
        ("peeling paint and damp stains on wall", "broken window", "crumbling wall"),
        ("building",),
    ),
    Category(
        "drainage",
        "Drainage and sewage",
        "Drains, nallahs, sewers, manholes and sewage plants",
        ("a photo of an open drain", "a photo of a sewage drain", "a photo of a manhole cover", "a photo of a storm water drain"),
        (
            "a photo of a clean, flowing drain",
            "a photo of a drain with some rubbish in it",
            "a photo of a blocked, overflowing drain full of garbage",
        ),
        (
            _d("garbage", "Garbage blocking the drain", "garbage and plastic waste in a drain", 0.35),
            _d("overflow", "Overflowing or stagnant water", "dirty stagnant overflowing water", 0.3),
            _d("cover", "Missing or broken cover", "an open manhole without a cover", 0.25),
            _d("wall", "Broken drain wall", "a broken concrete drain wall", 0.1),
        ),
        ("garbage and plastic waste", "dirty stagnant water", "broken concrete"),
        ("drainage", "sanitation"),
    ),
    Category(
        "water",
        "Public water infrastructure",
        "Public taps, hand pumps, water tanks, pipelines and treatment plants",
        (
            "a photo of a public water tap",
            "a photo of an overhead water tank",
            "a photo of a hand pump",
            "a photo of a water pipeline",
        ),
        (
            "a photo of a clean, working public water point",
            "a photo of a public water point with rust and minor leaks",
            "a photo of a broken, leaking public water point",
        ),
        (
            _d("leak", "Leaking pipe or tap", "water leaking from a broken pipe", 0.4),
            _d("rust", "Rust on tank or pipes", "rusty corroded metal", 0.25),
            _d("broken_tap", "Broken tap or hand pump", "a broken tap", 0.2),
            _d("dirt", "Dirt or algae around the water point", "green algae and dirt around water", 0.15),
        ),
        ("water leaking", "rust", "algae and dirt"),
        ("water_supply",),
    ),
    Category(
        "railways",
        "Railway infrastructure",
        "Stations, platforms, foot overbridges, level crossings and tracks",
        (
            "a photo of a railway station platform",
            "a photo of railway tracks",
            "a photo of a railway foot overbridge",
            "a photo of a level crossing",
        ),
        (
            "a photo of a clean, well maintained railway platform",
            "a photo of a railway platform with some damage and litter",
            "a photo of a railway platform with a broken surface, broken roof and garbage",
        ),
        (
            _d("surface", "Broken platform or floor surface", "a broken uneven floor", 0.3),
            _d("roof", "Damaged shelter or roof", "a damaged roof", 0.25),
            _d("garbage", "Garbage on tracks or platform", "garbage and litter", 0.25),
            _d("railing", "Damaged railing or fence", "a broken railing", 0.2),
        ),
        ("broken floor", "garbage and litter", "damaged roof"),
        ("railway",),
    ),
    Category(
        "public_buildings",
        "Public buildings",
        "Hospitals, health centres, anganwadis, offices and community halls",
        ("a photo of a government hospital building", "a photo of a public office building", "a photo of a community hall"),
        (
            "a photo of a clean, well maintained public building",
            "a photo of a public building with peeling paint and minor damage",
            "a photo of a run-down public building with broken windows and damaged walls",
        ),
        (
            _d("window", "Broken window or door", "a broken window", 0.25),
            _d("damp", "Damp, peeling or stained wall", "a damp wall with peeling paint", 0.3),
            _d("roof", "Damaged roof or ceiling", "a damaged roof", 0.25),
            _d("garbage", "Garbage around the building", "garbage and litter", 0.2),
        ),
        ("peeling paint and damp stains on wall", "broken window", "garbage"),
        ("building",),
    ),
    Category(
        "other",
        "Other public infrastructure",
        "Streetlights, bus shelters, public toilets, parks, footpaths and other public works",
        (
            "a photo of a bus shelter",
            "a photo of a street light",
            "a photo of a public toilet",
            "a photo of a public park",
            "a photo of a footpath",
        ),
        (
            "a photo of clean, well maintained public infrastructure",
            "a photo of public infrastructure with some damage",
            "a photo of broken, neglected public infrastructure",
        ),
        (
            _d("broken", "Broken part", "a broken structure", 0.4),
            _d("garbage", "Garbage", "garbage and litter", 0.3),
            _d("rust", "Rust", "rusty corroded metal", 0.3),
        ),
        ("broken structure", "garbage", "rust"),
        ("urban", "other", "power"),
    ),
)

BY_SLUG = {c.slug: c for c in CATEGORIES}
LABELS = {c.slug: c.label for c in CATEGORIES}

# Detected only to blur them before a photo is shown; never used for assessment.
PRIVACY_QUERIES = ("a human face", "a vehicle number plate")


def get(slug: str) -> Category:
    return BY_SLUG[slug]
