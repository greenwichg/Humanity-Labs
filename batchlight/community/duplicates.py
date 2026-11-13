"""Find photos already shared on the platform, so the same photo cannot become a second post.

Exact copies are caught by the SHA-256 of the file and of its decoded pixels. Re-saved, resized or slightly edited
copies are caught when both perceptual hashes are close: pHash within PHASH_MAX bits and dHash within DHASH_MAX bits
of 64. Requiring both keeps different photos of the same pothole from being treated as duplicates.

Crops slip past both hashes. Image similarity alone cannot tell a crop from a different photo of a similar pothole,
so a narrower rule is used after the models run: a photo by the same contributor, of the same category, within
REPEAT_METRES and REPEAT_HOURS of one of their earlier photos and at least REPEAT_SIMILARITY alike (CLIP), is marked
as a repeat. It stays visible but is counted once in rankings.
"""

from __future__ import annotations

import math
import uuid
from datetime import timedelta

import numpy as np
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from .models import CommunityImage, CommunityPost

PHASH_MAX = 8
DHASH_MAX = 10
REPEAT_SIMILARITY = 0.88  # crops keeping 75-90% of a photo measured 0.90-0.93; a 60% corner crop 0.86
REPEAT_METRES = 100
REPEAT_HOURS = 24


def find_duplicate(db: Session, sha256: str, pixel_sha256: str, phash: int, dhash: int) -> tuple[uuid.UUID, str] | None:
    """(post id, 'exact' or 'near') of an existing post with this photo, or None."""
    exact = db.execute(
        select(CommunityImage.post_id)
        .join(CommunityPost, CommunityPost.id == CommunityImage.post_id)
        .where(
            or_(CommunityImage.sha256 == sha256, CommunityImage.pixel_sha256 == pixel_sha256), CommunityPost.status != "removed"
        )
        .limit(1)
    ).scalar()
    if exact:
        return exact, "exact"
    near = db.execute(
        text(
            """
            SELECT i.post_id
            FROM community_images i JOIN community_posts p ON p.id = i.post_id
            WHERE p.status <> 'removed'
              AND bit_count((i.phash # :ph)::bit(64)) <= :pmax
              AND bit_count((i.dhash # :dh)::bit(64)) <= :dmax
            ORDER BY bit_count((i.phash # :ph)::bit(64)) LIMIT 1
            """
        ),
        {"ph": phash, "dh": dhash, "pmax": PHASH_MAX, "dmax": DHASH_MAX},
    ).scalar()
    return (near, "near") if near else None


def find_repeat(db: Session, post: CommunityPost, embedding: np.ndarray) -> uuid.UUID | None:
    """An earlier post by the same contributor that this photo most likely repeats, or None."""
    dlat = REPEAT_METRES / 111_320
    dlng = dlat / max(0.2, math.cos(math.radians(float(post.latitude))))
    since = post.created_at - timedelta(hours=REPEAT_HOURS)
    rows = db.execute(
        select(CommunityPost.id, CommunityImage.clip_embedding)
        .join(CommunityImage, CommunityImage.post_id == CommunityPost.id)
        .where(
            CommunityPost.author_id == post.author_id,
            CommunityPost.category == post.category,
            CommunityPost.id != post.id,
            CommunityPost.status.in_(("published", "processing")),
            CommunityPost.created_at.between(since, post.created_at),
            CommunityPost.latitude.between(float(post.latitude) - dlat, float(post.latitude) + dlat),
            CommunityPost.longitude.between(float(post.longitude) - dlng, float(post.longitude) + dlng),
            CommunityImage.clip_embedding.is_not(None),
        )
    ).all()
    best, best_id = REPEAT_SIMILARITY, None
    for pid, emb in rows:
        sim = float(np.asarray(emb, dtype=np.float32) @ embedding)
        if sim >= best:
            best, best_id = sim, pid
    return best_id
