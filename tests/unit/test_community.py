"""Community: photo checks, duplicate hashes, the scoring engine, Community Notes, constituency geometry, project
evidence rules and storage safety. No database and no vision models needed."""

from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from batchlight.community import assess, notes, projects, reps
from batchlight.community import categories as C
from batchlight.community.vision import image as I


def _photo(seed: int = 1, size=(800, 600), fmt="JPEG", exif=None) -> bytes:
    """A textured test image (smooth noise), sharp and bright enough to pass the quality checks."""
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 220, (size[1] // 16, size[0] // 16, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)
    noise = rng.integers(-25, 25, (size[1], size[0], 3))
    img = Image.fromarray(np.clip(np.asarray(img, dtype=int) + noise, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, fmt, **({"exif": exif} if exif is not None else {}))
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ photo checks
def test_prepare_strips_metadata_and_reads_gps():
    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (17.0, 26.0, 24.36), 3: "E", 4: (78.0, 20.0, 56.04)})
    exif[0x010F] = "PhoneMaker"
    prepared = I.prepare(_photo(exif=exif))
    assert prepared.taken_lat == pytest.approx(17.4401, abs=1e-4)
    assert prepared.taken_lng == pytest.approx(78.3489, abs=1e-4)
    stored = Image.open(io.BytesIO(prepared.data))
    assert stored.format == "JPEG" and not stored.getexif()  # no GPS, no camera details in what is kept
    assert prepared.quality.usable


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not an image", "not a photo we can read"),
        (_photo(size=(300, 200)), "too small"),
        (_photo(fmt="GIF"), "JPEG, PNG or WebP"),
    ],
)
def test_prepare_rejects_unusable_files(raw, message):
    with pytest.raises(I.ImageRejected, match=message):
        I.prepare(raw)


def test_prepare_rejects_black_and_blurred_photos():
    black = io.BytesIO()
    Image.new("RGB", (800, 600), (3, 3, 3)).save(black, "JPEG")
    with pytest.raises(I.ImageRejected):
        I.prepare(black.getvalue())
    flat = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 120, 120)).save(flat, "JPEG")
    with pytest.raises(I.ImageRejected, match="blurred"):
        I.prepare(flat.getvalue())


def test_hashes_match_a_resaved_copy_but_not_another_photo():
    a = I.prepare(_photo(seed=1))
    img = Image.open(io.BytesIO(_photo(seed=1))).resize((640, 480))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=60)
    copy = I.prepare(buf.getvalue())
    other = I.prepare(_photo(seed=2))
    assert copy.sha256 != a.sha256
    assert I.hamming(a.phash, copy.phash) <= 8 and I.hamming(a.dhash, copy.dhash) <= 10
    assert I.hamming(a.phash, other.phash) > 8 or I.hamming(a.dhash, other.dhash) > 10


def test_blur_changes_only_the_region():
    data = I.prepare(_photo()).data
    out = np.asarray(Image.open(io.BytesIO(I.blur_regions(data, [(0.4, 0.4, 0.1, 0.1)]))), dtype=int)
    src = np.asarray(Image.open(io.BytesIO(data)), dtype=int)
    h, w = src.shape[:2]
    inside = (slice(int(0.42 * h), int(0.48 * h)), slice(int(0.42 * w), int(0.48 * w)))
    corner = (slice(0, int(0.2 * h)), slice(0, int(0.2 * w)))
    assert np.abs(out[inside] - src[inside]).mean() > 5
    assert np.abs(out[corner] - src[corner]).mean() < 3


# ------------------------------------------------------------------------------------------------ scoring
def _obs(category="roads", poor=0.9, fair=0.05, area=0.1, fit=1.0, usable=True, detections=()):
    good = 1 - poor - fair
    probs = {c.slug: 0.0 for c in C.CATEGORIES}
    probs[category] = 0.9
    if fit < 1:
        probs["schools"] = 0.9 / max(fit, 1e-6)
    top = max(probs, key=probs.get)
    return assess.Observation(
        category=category,
        quality={"usable": usable, "problems": [] if usable else ["The photo looks blurred or out of focus."]},
        scene={"probabilities": probs, "chosen": probs[category], "top": top, "fit": fit},
        condition={"good": good, "fair": fair, "poor": poor},
        detections=list(detections),
        segmentation={"prompts": {}, "area": area, "threshold": assess.SEG_THRESHOLD},
    )


def test_score_is_100_minus_listed_points():
    r = assess.score(_obs())
    assert r.score == pytest.approx(100 - sum(c["points"] for c in r.criteria), abs=0.2)
    assert [c["key"] for c in r.criteria] == ["condition", "defects", "area"]
    assert all(c["evidence"] and c["method"] for c in r.criteria)


def test_detected_defects_are_shown_but_carry_no_points():
    det = [{"defect": "pothole", "label": "Pothole", "score": 0.9, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}]
    with_boxes, without = assess.score(_obs(detections=det)), assess.score(_obs())
    assert with_boxes.score == without.score
    defects = next(c for c in with_boxes.criteria if c["key"] == "defects")
    assert defects["max"] == 0 and defects["points"] == 0 and "Pothole: 1 found" in defects["evidence"]


def test_worse_visible_condition_gives_a_lower_score():
    assert assess.score(_obs(poor=0.9, fair=0.05, area=0.2)).score < assess.score(_obs(poor=0.05, fair=0.1, area=0.0)).score


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [({"usable": False}, "not clear enough"), ({"fit": 0.05}, "may not show roads")],
)
def test_photos_that_cannot_be_judged_are_not_scored(kwargs, reason):
    r = assess.score(_obs(**kwargs))
    assert r.score is None and reason in r.not_scored_reason


def test_every_category_has_its_own_criteria():
    assert len({c.condition_prompts for c in C.CATEGORIES}) == len(C.CATEGORIES)
    for c in C.CATEGORIES:
        assert c.defects and c.damage_prompts and c.scene_prompts
        assert sum(c.weights.values()) == 100


def test_descriptions_never_judge_safety():
    text = "A road with a large pothole. The bridge looks structurally unsafe. Water is pooled on the left. It may collapse"
    assert assess.clean_description(text) == "A road with a large pothole. Water is pooled on the left."


# ------------------------------------------------------------------------------------------------ community notes
def _ratings():
    a, b = [f"a{i}" for i in range(10)], [f"b{i}" for i in range(10)]
    r = []
    for k in range(6):  # each group's usual pattern: helpful to their side's notes, not the other side's
        r += [(u, f"pa{k}", 1.0) for u in a] + [(u, f"pa{k}", 0.0) for u in b]
        r += [(u, f"pb{k}", 1.0) for u in b] + [(u, f"pb{k}", 0.0) for u in a]
    r += [(u, "bridge", 1.0) for u in a + b]
    r += [(u, "split", 1.0) for u in a[:5]] + [(u, "split", 0.0) for u in b[:5]]
    r += [(u, "bad", 0.0) for u in a + b]
    r += [(u, "few", 1.0) for u in a[:3]]
    return r


@pytest.mark.parametrize(
    ("note", "status"),
    [("bridge", "helpful"), ("split", "disputed"), ("bad", "not_helpful"), ("few", "pending")],
)
def test_note_status_needs_agreement_across_perspectives(note, status):
    r = _ratings()
    assert notes.decide(note, r, notes.fit(r))[0] == status


def test_one_sided_support_is_not_enough():
    r = _ratings()
    f = notes.fit(r)
    assert notes.decide("pa0", r, f)[0] != "helpful"  # all of one group found it helpful, the other did not


def test_note_statuses_are_reproducible():
    r = _ratings()
    assert notes.fit(r).note_intercept == notes.fit(r).note_intercept


# ------------------------------------------------------------------------------------------------ representatives
def test_point_in_polygon_with_a_hole_and_multipolygon():
    square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    poly = {"type": "Polygon", "coordinates": [square, hole]}
    assert reps.contains(poly, 2, 2) and not reps.contains(poly, 5, 5) and not reps.contains(poly, 11, 5)
    multi = {"type": "MultiPolygon", "coordinates": [[square], [[[20, 20], [30, 20], [30, 30], [20, 20]]]]}
    assert reps.contains(multi, 28, 22) and not reps.contains(multi, 15, 15)


# ------------------------------------------------------------------------------------------------ official records
def test_references_compare_without_spacing_or_punctuation():
    assert projects.normalise("ap07 vii-26") == projects.normalise("AP07VII26") == "AP07VII26"


def _post(state="telangana", category="roads"):
    return SimpleNamespace(official_reference="AP07VII26", state_slug=state, category=category)


def test_link_needs_same_state_and_fitting_work():
    road = SimpleNamespace(state_slugs=["telangana"], asset_type="road")
    assert projects.evidence_for(_post(), road)[0]
    assert not projects.evidence_for(_post(state="karnataka"), road)[0]
    assert not projects.evidence_for(_post(category="schools"), road)[0]
    assert not projects.evidence_for(_post(), SimpleNamespace(state_slugs=[], asset_type="road"))[0]


def test_financial_caveat_never_claims_the_section_cost():
    assert "not the cost of the stretch or building in this photo" in projects.CAVEAT


# ------------------------------------------------------------------------------------------------ storage
def test_storage_refuses_unsafe_keys(tmp_path):
    from batchlight.community.storage import LocalStorage

    s = LocalStorage(tmp_path)
    s.put("posts/abc/photo.jpg", b"x")
    assert s.get("posts/abc/photo.jpg") == b"x"
    for bad in ("../etc/passwd", "/abs/path", "posts/../../x", "Posts/UPPER"):
        with pytest.raises(ValueError):
            s.put(bad, b"x")


def test_storage_never_falls_back_to_local_disk_when_a_bucket_is_set(monkeypatch):
    from batchlight.community import storage as S
    from batchlight.config import get_settings

    monkeypatch.setenv("BATCHLIGHT_MEDIA_S3_BUCKET", "humanity-labs-media-111122223333")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="S3 photo store"):
            S.storage()
    finally:
        monkeypatch.delenv("BATCHLIGHT_MEDIA_S3_BUCKET")
        get_settings.cache_clear()
