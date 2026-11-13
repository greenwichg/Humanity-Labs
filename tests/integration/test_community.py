"""Community end to end against PostgreSQL: sharing a photo, duplicates, processing, notes, discussion, rankings,
moderation and the production switch. The vision models are replaced by a small stub, so no model files are needed."""

from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import func, select

from tests.conftest import post_form


def _photo(seed: int = 1, size=(800, 600)) -> bytes:
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 220, (size[1] // 16, size[0] // 16, 3), dtype=np.uint8)
    img = Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)
    noise = rng.integers(-25, 25, (size[1], size[0], 3))
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.asarray(img, dtype=int) + noise, 0, 255).astype(np.uint8)).save(buf, "JPEG")
    return buf.getvalue()


class _Clip:
    def image_embedding(self, img):
        v = np.resize(np.asarray(img.resize((16, 16)), dtype=np.float32).ravel(), 512)
        return v / np.linalg.norm(v)

    def probabilities(self, emb, prompts):
        from batchlight.community import categories as C

        if len(prompts) == 3:  # good, fair, poor
            return np.array([0.05, 0.15, 0.80])
        road = set(C.get("roads").scene_prompts) | set(C.get("bridges").scene_prompts)
        p = np.array([1.0 if pr in road else 0.01 for pr in prompts])
        return p / p.sum()


class _Owl:
    def detect(self, img, queries, threshold, max_per_query=8):
        return []


class _Seg:
    def probabilities(self, img, prompts):
        maps = np.zeros((len(prompts), 352, 352), dtype=np.float32)
        maps[0, 100:180, 100:180] = 0.9
        return maps


class _Florence:
    def describe(self, img):
        return "A road with a large pothole near the kerb. The road surface is unsafe to drive on."


MODELS = SimpleNamespace(clip=_Clip(), owl=_Owl(), clipseg=_Seg(), florence=_Florence())


@pytest.fixture
def media(tmp_path, monkeypatch):
    from batchlight.config import get_settings

    monkeypatch.setenv("BATCHLIGHT_MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def join(client, n: int):
    r = post_form(
        client,
        "/community/join",
        {"full_name": f"Contributor {n}", "email": f"contributor{n}@example.org", "password": "correct horse battery 9"},
    )
    assert r.status_code == 303, r.text[:300]


def share(client, raw: bytes, **fields):
    data = {
        "category": "roads",
        "title": "Potholes on Station Road near the bus stand",
        "latitude": "17.4401",
        "longitude": "78.3489",
        "location_source": "pin",
        "state_slug": "telangana",
        "district": "Rangareddy",
        "locality_kind": "city",
        "locality_name": "Hyderabad",
        "consent": "yes",
        **fields,
    }
    return post_form(client, "/community/new", data, files={"photo": ("photo.jpg", raw, "image/jpeg")})


def process(db, post_id):
    from batchlight.community.pipeline import process_post

    assert process_post(db, post_id, models=MODELS) == "published"
    db.commit()


def post_id_from(response):
    import uuid

    return uuid.UUID(response.headers["location"].rsplit("/", 1)[-1].split("?")[0])


@pytest.fixture
def people(make_client, media):
    clients = []
    for n in range(7):
        c = make_client()
        join(c, n)
        clients.append(c)
    return clients


def test_share_process_and_view(db, people):
    from batchlight.community.models import CommunityAssessment, CommunityImage

    r = share(people[0], _photo())
    assert r.status_code == 303
    pid = post_id_from(r)
    process(db, pid)
    page = people[1].get(f"/community/posts/{pid}")
    assert page.status_code == 200
    a = db.scalar(select(CommunityAssessment).where(CommunityAssessment.post_id == pid))
    assert a.score is not None and f"{a.score:.0f}" in page.text
    assert "large pothole" in page.text and "unsafe" not in a.description  # safety judgments are removed
    assert "It does not say whether a structure is safe" in page.text
    image = db.scalar(select(CommunityImage).where(CommunityImage.post_id == pid))
    assert len(image.clip_embedding) == 512
    photo = people[1].get(f"/community/media/{pid}/photo")
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert not Image.open(io.BytesIO(photo.content)).getexif()


def test_duplicates_are_refused(db, people):
    raw = _photo(seed=3)
    assert share(people[0], raw).status_code == 303
    again = share(people[1], raw)
    assert again.status_code == 409 and "already been shared" in again.text
    img = Image.open(io.BytesIO(raw)).resize((700, 525))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    resaved = share(people[2], buf.getvalue(), title="Same place, saved again as PNG")
    assert resaved.status_code == 409 and "almost identical" in resaved.text


def test_notes_and_discussion_never_change_the_score(db, people):
    from batchlight.community.models import CommunityAssessment, CommunityComment, CommunityNote

    pid = post_id_from(share(people[0], _photo(seed=4)))
    process(db, pid)
    before = db.scalar(select(CommunityAssessment.score).where(CommunityAssessment.post_id == pid))
    assert (
        post_form(
            people[1],
            f"/community/posts/{pid}/notes",
            {"kind": "correction", "body": "This stretch was resurfaced last week; the photo is older.", "sources": ""},
        ).status_code
        == 303
    )
    note = db.scalar(select(CommunityNote).where(CommunityNote.post_id == pid))
    assert post_form(people[1], f"/community/notes/{note.id}/rate", {"value": "helpful"}).status_code == 303  # own note: refused
    for c in people[2:7]:
        assert post_form(c, f"/community/notes/{note.id}/rate", {"value": "not_helpful"}).status_code == 303
    r = post_form(people[2], f"/community/posts/{pid}/comments", {"body": "Which side of the road is this?"})
    parent = db.scalar(select(CommunityComment.id).where(CommunityComment.post_id == pid))
    assert (
        post_form(
            people[3], f"/community/posts/{pid}/comments", {"body": "The left side, near the depot.", "parent_id": str(parent)}
        ).status_code
        == 303
    )
    assert post_form(people[4], f"/community/comments/{parent}/vote", {"value": "1"}).status_code == 303
    db.expire_all()
    note = db.get(CommunityNote, note.id)
    assert note.ratings_count == 5 and note.status != "pending"
    assert db.scalar(select(func.count()).select_from(CommunityAssessment).where(CommunityAssessment.post_id == pid)) == 1
    assert db.scalar(select(CommunityAssessment.score).where(CommunityAssessment.post_id == pid)) == before
    page = people[5].get(f"/community/posts/{pid}?open=discussion")
    assert "The left side, near the depot." in page.text and "Notes never change the score" in page.text
    assert r.status_code == 303


def test_rankings_need_enough_photos_and_never_mix_categories(db, people):
    from batchlight.community import rankings as R

    for i in range(4):
        process(db, post_id_from(share(people[i % 2], _photo(seed=10 + i), latitude=f"17.44{i}")))
    process(db, post_id_from(share(people[3], _photo(seed=30), category="bridges", title="Cracks on the bridge railing")))
    [city] = R.units(db, "roads", "city")
    assert city.n == 4 and not city.ranked and "5" in city.why_not_ranked
    process(db, post_id_from(share(people[2], _photo(seed=20), latitude="17.4501")))
    [city] = R.units(db, "roads", "city")
    assert city.n == 5 and city.ranked and city.margin is not None
    [bridges] = R.units(db, "bridges", "city")
    assert bridges.n == 1  # the bridge photo counts only in the bridge rankings
    assert people[0].get("/community/rankings?category=roads&level=state").status_code == 200


def test_a_repeat_by_the_same_person_counts_once(db, people):
    from batchlight.community import rankings as R
    from batchlight.community.models import CommunityPost

    raw = _photo(seed=40)
    first = post_id_from(share(people[0], raw))
    img = Image.open(io.BytesIO(raw))
    buf = io.BytesIO()
    img.crop((0, 0, 800, 540)).save(buf, "JPEG")  # a crop slips past the fingerprints
    second = post_id_from(share(people[0], buf.getvalue(), title="Potholes on Station Road, closer", latitude="17.4402"))
    process(db, first)
    process(db, second)
    assert db.get(CommunityPost, second).repeat_of == first
    [city] = R.units(db, "roads", "city")
    assert city.n == 1


def test_hidden_posts_disappear(db, people):
    from batchlight.community.models import CommunityReport
    from batchlight.models import User

    pid = post_id_from(share(people[0], _photo(seed=50)))
    process(db, pid)
    assert (
        post_form(
            people[1],
            f"/community/posts/{pid}/report",
            {"target_type": "post", "reason": "privacy", "details": "A face is visible."},
        ).status_code
        == 303
    )
    assert people[1].get("/community/moderation").status_code == 403
    db.execute(User.__table__.update().where(User.email == "contributor6@example.org").values(is_curator=True))
    db.commit()
    report = db.scalar(select(CommunityReport))
    assert post_form(people[6], f"/community/moderation/{report.id}", {"action": "hide"}).status_code == 303
    assert people[2].get(f"/community/posts/{pid}").status_code == 404
    assert people[2].get(f"/community/media/{pid}/photo").status_code == 404


def test_uploads_can_be_switched_off(db, people, monkeypatch):
    from batchlight.config import Settings, get_settings

    assert Settings(env="production", secret_key="x" * 40).uploads_enabled is False
    monkeypatch.setenv("BATCHLIGHT_COMMUNITY_UPLOADS", "false")
    get_settings.cache_clear()
    assert share(people[0], _photo(seed=60)).status_code == 403


def test_methodology_page_lists_models_and_thresholds(client):
    page = client.get("/community/about")
    assert page.status_code == 200
    for text in ("Xenova/clip-vit-base-patch32", "Florence-2", "How the thresholds were set", "never combined"):
        assert text in page.text
