"""Turn what the vision models observed in a photo into a visible-condition score with confidence and reasons.

The score describes only what is visible in one photo. It never claims a structure is safe or unsafe, never infers
hidden or structural defects, and is never combined across categories. Every point taken off is listed as a
criterion with its evidence, so the score can be checked by anyone.

`observe` runs the models; `score` is a pure function of the observations, so it can be tested and audited alone.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import categories as C

ENGINE_VERSION = "community-assessment/2"

# Set from a test on 80 labelled pothole photos and 21 ordinary street photos (docs/COMMUNITY.md, "How the thresholds
# were set"). The test covered roads only; the other categories use the same values until they have their own test.
DETECT_THRESHOLD = 0.30  # OWLv2 boxes below this are not shown (44% of shown pothole boxes matched a labelled pothole)
PRIVACY_THRESHOLD = 0.12  # lower on purpose: blurring a non-face costs little, missing a face costs privacy
SEG_THRESHOLD = 0.50  # CLIPSeg probability above which a pixel is outlined (best overlap with labelled potholes)
AREA_FULL = 0.20  # outlined share of the photo at which the outline criterion takes its full weight
NOT_SCORED_FIT = 0.15  # below this category fit the photo may not show the chosen kind of infrastructure

# What that test measured, shown on the methodology page so the limits are visible next to the thresholds.
CALIBRATION = (
    ("Test photos", "80 road photos with 110 potholes outlined by hand (CC BY 4.0) and 21 ordinary street photos (CC0)."),
    ("Kind of infrastructure", "All 80 pothole photos were recognised as roads."),
    (
        "Visible condition",
        "Rated more likely poor than not for 96% of the pothole photos and 5% of the street photos.",
    ),
    (
        "Possible defects",
        "At the 30% threshold a pothole box appeared on only 17% of the pothole photos, and a defect box on 19% of the "
        "street photos; 44% of the pothole boxes matched a labelled pothole. Too weak to score, so the boxes carry no points.",
    ),
    (
        "Damage-like regions",
        "At the 50% threshold the outline overlapped the labelled potholes by 27% on average and its size did not follow "
        "theirs; on most street photos it outlined nothing. It shows where damage appears, not how much there is.",
    ),
    (
        "Scores",
        "About 15 for the median pothole photo and 76 for the median street photo. The set is small and its damage is "
        "obvious: mild wear, and categories other than roads, have not been tested yet.",
    ),
)

NEUTRAL_QUERIES = ("a car", "a person", "a tree", "the sky")
SAFETY_WORDS = re.compile(
    r"\b(unsafe|safe|safety|dangerous|danger|collapse\w*|structural\w*|hazard\w*|risk\w*|fail\w*|integrity)\b", re.IGNORECASE
)


@dataclass
class Observation:
    category: str
    quality: dict
    scene: dict  # {"probabilities": {slug: p}, "chosen": p, "top": slug, "fit": 0-1}
    condition: dict  # {"good": p, "fair": p, "poor": p}
    detections: list[dict]  # {"defect", "label", "score", "x", "y", "w", "h"}
    segmentation: dict  # {"prompts": {prompt: area}, "area": union share, "threshold": t}
    description: str | None = None
    mask: np.ndarray | None = field(default=None, repr=False)
    embedding: np.ndarray | None = field(default=None, repr=False)  # CLIP image vector, normalised


@dataclass
class Result:
    score: float | None
    confidence: float
    confidence_label: str
    criteria: list[dict]
    observations: list[str]
    not_scored_reason: str | None


def _pct(v: float) -> str:
    return f"{round(v * 100)}%"


def clean_description(text: str | None) -> str | None:
    """Keep whole sentences that describe what is visible; drop any sentence that judges safety or structure."""
    if not text:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if sentences and not re.search(r"[.!?]$", sentences[-1]):
        sentences = sentences[:-1]  # cut off by the length limit
    kept = [s for s in sentences if s and not SAFETY_WORDS.search(s)]
    return " ".join(kept) or None


def defect_strength(scores: list[float]) -> float:
    """How strongly the detections of one defect type suggest it is present: 0 (none) to 1."""
    p = [min(1.0, max(0.0, (s - DETECT_THRESHOLD) / (1 - DETECT_THRESHOLD)) * 0.7 + 0.3) for s in scores if s >= DETECT_THRESHOLD]
    if not p:
        return 0.0
    return float(1 - np.prod([1 - x for x in p]))


def score(obs: Observation) -> Result:
    cat = C.get(obs.category)
    w = cat.weights
    q = obs.quality or {}
    criteria: list[dict] = []
    observations: list[str] = []

    fit = float(obs.scene.get("fit", 0.0))
    top = obs.scene.get("top")
    if top and top != obs.category:
        observations.append(
            f"The photo looks most like {C.LABELS.get(top, top).lower()} ({_pct(obs.scene['probabilities'][top])}); {cat.label.lower()} scored {_pct(obs.scene.get('chosen', 0))}."
        )
    else:
        observations.append(
            f"The photo looks like {cat.label.lower()} ({_pct(obs.scene.get('chosen', 0))} among all categories)."
        )

    # 1. Visible condition, as classified by CLIP against this category's own descriptions.
    good, fair, poor = (float(obs.condition.get(k, 0.0)) for k in ("good", "fair", "poor"))
    severity = poor + 0.4 * fair
    cond_points = round(w["condition"] * severity, 1)
    criteria.append(
        {
            "key": "condition",
            "label": "Visible condition",
            "max": w["condition"],
            "points": cond_points,
            "evidence": f"Image classification: good {_pct(good)}, fair {_pct(fair)}, poor {_pct(poor)}.",
            "method": "CLIP compares the photo with descriptions of this category in good, fair and poor condition.",
        }
    )

    # 2. Visible defects, as detected by OWLv2 with this category's own defect list.
    by_type: dict[str, list[float]] = {}
    for d in obs.detections:
        by_type.setdefault(d["defect"], []).append(float(d["score"]))
    parts, def_points = [], 0.0
    for defect in cat.defects:
        scores = sorted(by_type.get(defect.key, []), reverse=True)
        def_points += w["defects"] * defect.weight * defect_strength(scores)
        if scores:
            parts.append(f"{defect.label}: {len(scores)} found (highest {_pct(scores[0])})")
            noun = defect.label.lower()
            observations.append(
                f"{len(scores)} possible {noun}{'' if len(scores) == 1 else ' areas'} detected (highest confidence {_pct(scores[0])})."
            )
    criteria.append(
        {
            "key": "defects",
            "label": "Possible defects (shown, not scored)" if not w["defects"] else "Visible defects",
            "max": w["defects"],
            "points": round(def_points, 1),
            "evidence": "; ".join(parts) if parts else "No defects from this category's list were detected above the threshold.",
            "method": "OWLv2 looks for: "
            + ", ".join(d.label.lower() for d in cat.defects)
            + f". Boxes below {_pct(DETECT_THRESHOLD)} confidence are ignored."
            + (
                " The boxes help people look, but carry no points: in our test the detector was not reliable enough."
                if not w["defects"]
                else ""
            ),
        }
    )

    # 3. Visibly damaged area, as segmented by CLIPSeg.
    area = float(obs.segmentation.get("area", 0.0))
    area_points = round(w["area"] * min(1.0, area / AREA_FULL), 1)
    criteria.append(
        {
            "key": "area",
            "label": "Damage-like regions",
            "max": w["area"],
            "points": area_points,
            "evidence": f"About {_pct(area)} of the photo is outlined as looking damaged.",
            "method": "CLIPSeg outlines regions matching: "
            + ", ".join(cat.damage_prompts)
            + f". The full weight applies at {_pct(AREA_FULL)} of the photo. The outline shows where damage appears, "
            "not its true size: in our test it was usually larger than the damage itself.",
        }
    )
    if area >= 0.01:
        observations.append(
            f"Damage-like regions are outlined in about {_pct(area)} of the photo (shown in red when the overlay is on)."
        )

    raw = max(0.0, min(100.0, 100.0 - cond_points - def_points - area_points))

    # Confidence: is the photo usable, does it show the chosen category, and do the three measures agree?
    quality_factor = 1.0 if q.get("usable", True) else 0.5
    agreement = 1.0 - abs(severity - min(1.0, area / AREA_FULL))  # do the classifier and the outline agree?
    confidence = round(0.35 * quality_factor + 0.35 * min(1.0, fit) + 0.30 * agreement, 3)
    label = "high" if confidence >= 0.75 else "medium" if confidence >= 0.5 else "low"

    not_scored = None
    if not q.get("usable", True):
        not_scored = "The photo is not clear enough to score: " + " ".join(q.get("problems", [])).strip()
    elif fit < NOT_SCORED_FIT:
        not_scored = f"The photo may not show {cat.label.lower()}: the models found it most like {C.LABELS.get(top, 'something else').lower()}."
    return Result(None if not_scored else round(raw, 1), confidence, label, criteria, observations, not_scored)


# ------------------------------------------------------------------------------------------------ running the models
def scene_probabilities(clip, emb) -> dict[str, float]:
    prompts = [p for c in C.CATEGORIES for p in c.scene_prompts]
    owners = [c.slug for c in C.CATEGORIES for _ in c.scene_prompts]
    probs = clip.probabilities(emb, prompts)
    out: dict[str, float] = {}
    for o, p in zip(owners, probs, strict=True):
        out[o] = out.get(o, 0.0) + float(p)
    return out


# A face or plate box larger than this share of the photo is usually a whole person or vehicle. Blurring all of it
# would hide the road or building behind, so the detector looks again inside it for the face or plate itself.
PRIVACY_MAX_AREA = 0.006
PRIVACY_RECHECKS = 8  # the largest boxes are looked at again; any beyond this fall back to a partial blur


def _fallback(kind: int, b) -> tuple[float, float, float, float]:
    """Where a face or plate must be when a second look finds nothing: the top of a person, the lower part of a vehicle."""
    if kind == 0:
        return (b.x + b.w * 0.2, b.y, b.w * 0.6, min(b.h, b.w) * 0.6)
    return (b.x + b.w * 0.1, b.y + b.h * 0.5, b.w * 0.8, b.h * 0.5)


def privacy_boxes(models, img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Regions to blur before a photo is published, as fractions of the image (x, y, width, height)."""
    boxes = models.owl.detect(img, list(C.PRIVACY_QUERIES), threshold=PRIVACY_THRESHOLD, max_per_query=30)
    small = [b for b in boxes if b.w * b.h <= PRIVACY_MAX_AREA]
    large = sorted((b for b in boxes if b.w * b.h > PRIVACY_MAX_AREA), key=lambda b: b.w * b.h, reverse=True)
    out = [(b.x, b.y, b.w, b.h) for b in small]
    W, H = img.size
    for i, b in enumerate(large):
        if i >= PRIVACY_RECHECKS:
            out.append(_fallback(b.query, b))
            continue
        left, top = int(b.x * W), int(b.y * H)
        crop = img.crop((left, top, max(left + 1, int((b.x + b.w) * W)), max(top + 1, int((b.y + b.h) * H))))
        cw, ch = crop.size
        inner = [
            c
            for c in models.owl.detect(crop, [C.PRIVACY_QUERIES[b.query]], threshold=PRIVACY_THRESHOLD, max_per_query=6)
            if c.w * c.h <= 0.5
        ]
        if not inner:
            out.append(_fallback(b.query, b))
        for c in inner:
            out.append(((left + c.x * cw) / W, (top + c.y * ch) / H, c.w * cw / W, c.h * ch / H))
    return out


def observe(models, img: Image.Image, category: str, quality: dict, describe: bool = True) -> tuple[Observation, float]:
    started = time.monotonic()
    cat = C.get(category)
    emb = models.clip.image_embedding(img)
    probs = scene_probabilities(models.clip, emb)
    best = max(probs.values())
    scene = {
        "probabilities": {k: round(v, 4) for k, v in probs.items()},
        "chosen": round(probs[category], 4),
        "top": max(probs, key=probs.get),
        "fit": round(probs[category] / best, 4) if best > 0 else 0.0,
    }
    cond = models.clip.probabilities(emb, list(cat.condition_prompts))
    condition = {"good": round(float(cond[0]), 4), "fair": round(float(cond[1]), 4), "poor": round(float(cond[2]), 4)}

    queries = [d.query for d in cat.defects] + list(NEUTRAL_QUERIES)
    boxes = models.owl.detect(img, queries, threshold=DETECT_THRESHOLD)
    neutral = [b for b in boxes if b.query >= len(cat.defects)]
    detections = []
    for b in boxes:
        if b.query >= len(cat.defects):
            continue
        # A "defect" box that is mostly a car, a person, a tree or sky, with a higher score, is not a defect.
        from .vision.runtime import _iou

        if any(_iou(b, n) > 0.5 and n.score > b.score for n in neutral):
            continue
        d = cat.defects[b.query]
        detections.append(
            {
                "defect": d.key,
                "label": d.label,
                "score": round(b.score, 3),
                "x": round(b.x, 4),
                "y": round(b.y, 4),
                "w": round(b.w, 4),
                "h": round(b.h, 4),
            }
        )

    maps = models.clipseg.probabilities(img, list(cat.damage_prompts))
    masks = maps > SEG_THRESHOLD
    union = masks.any(axis=0)
    segmentation = {
        "prompts": {p: round(float(m.mean()), 4) for p, m in zip(cat.damage_prompts, masks, strict=True)},
        "area": round(float(union.mean()), 4),
        "threshold": SEG_THRESHOLD,
    }
    description = clean_description(models.florence.describe(img)) if describe else None
    obs = Observation(category, quality, scene, condition, detections, segmentation, description, union)
    obs.embedding = emb / (np.linalg.norm(emb) or 1.0)
    return obs, time.monotonic() - started
