"""Background processing of new posts, run by the worker (or `python -m batchlight.cli community-process`).

For each post waiting in 'processing': blur faces and number plates, assess the photo, tag the area's elected
representatives, check any reference number against official records, then publish. A post is never published
unblurred: if the models are not available it stays in processing and is retried.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import assess, duplicates, projects, reps
from .models import CommunityAssessment, CommunityImage, CommunityPost
from .storage import keys, storage
from .vision import models as vision_models
from .vision.image import blur_regions, thumbnail
from .vision.runtime import VisionModels, load_image, mask_png

log = logging.getLogger(__name__)


def assess_post(
    db: Session, post: CommunityPost, image: CommunityImage, models: VisionModels, describe: bool = True
) -> CommunityAssessment:
    store = storage()
    img = load_image(store.get(image.storage_key))
    obs, seconds = assess.observe(models, img, post.category, image.quality, describe=describe)
    result = assess.score(obs)
    mask_key = None
    if obs.mask is not None and obs.mask.any():
        mask_key = keys(post.id)["mask"]
        store.put(mask_key, mask_png(obs.mask))
    db.execute(update(CommunityAssessment).where(CommunityAssessment.post_id == post.id).values(is_current=False))
    a = CommunityAssessment(
        post_id=post.id,
        is_current=True,
        category=post.category,
        engine_version=assess.ENGINE_VERSION,
        model_versions=VisionModels.versions(),
        quality=image.quality,
        scene=obs.scene,
        condition=obs.condition,
        detections=obs.detections,
        segmentation=obs.segmentation,
        mask_key=mask_key,
        description=obs.description,
        description_model=vision_models.MODELS["florence2"].label if obs.description else None,
        observations=result.observations,
        criteria=result.criteria,
        score=result.score,
        confidence=result.confidence,
        confidence_label=result.confidence_label,
        not_scored_reason=result.not_scored_reason,
        seconds=round(seconds, 1),
    )
    db.add(a)
    if obs.embedding is not None:
        image.clip_embedding = [round(float(v), 5) for v in obs.embedding]
        post.repeat_of = duplicates.find_repeat(db, post, obs.embedding)
    return a


def process_post(db: Session, post_id: uuid.UUID, models: VisionModels | None = None) -> str:
    post = db.get(CommunityPost, post_id)
    image = db.scalar(select(CommunityImage).where(CommunityImage.post_id == post_id))
    if post is None or image is None:
        return "missing"
    models = models or VisionModels.get()
    store = storage()
    # 1. Privacy first: blur faces and number plates in the stored photo and its thumbnail.
    boxes = assess.privacy_boxes(models, load_image(store.get(image.storage_key)))
    if boxes:
        blurred = blur_regions(store.get(image.storage_key), boxes)
        store.put(image.storage_key, blurred)
        store.put(image.thumb_key, thumbnail(blurred))
    image.privacy_blurred = len(boxes)
    # 2. Assessment, 3. representatives, 4. official records. Each is separate; none reads another's output.
    assess_post(db, post, image, models)
    reps.tag(db, post)
    projects.link(db, post, post.author_id)
    post.analysis_status = "done"
    post.analysis_error = None
    if post.status == "processing":
        post.status = "published"
        post.published_at = datetime.now(UTC)
    return "published"


def process_pending(db_factory, limit: int = 10) -> dict[str, int]:
    """Process up to `limit` waiting posts. Returns counts by outcome."""
    counts = {"published": 0, "failed": 0, "waiting": 0}
    if not vision_models.ready():
        with db_factory() as db:
            counts["waiting"] = len(db.scalars(select(CommunityPost.id).where(CommunityPost.status == "processing")).all())
        if counts["waiting"]:
            log.warning(
                "community: %d posts waiting; vision models are not downloaded (run: python -m batchlight.cli community-models download)",
                counts["waiting"],
            )
        return counts
    with db_factory() as db:
        ids = db.scalars(
            select(CommunityPost.id)
            .where(CommunityPost.status == "processing", CommunityPost.analysis_status == "pending")
            .order_by(CommunityPost.created_at)
            .limit(limit)
        ).all()
    for pid in ids:
        try:
            with db_factory() as db:
                process_post(db, pid)
                db.commit()
            counts["published"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad photo must not stop the others
            log.exception("community: processing post %s failed", pid)
            with db_factory() as db:
                post = db.get(CommunityPost, pid)
                if post:
                    post.analysis_status = "failed"
                    post.analysis_error = str(exc)[:500]
                    db.commit()
            counts["failed"] += 1
    return counts
