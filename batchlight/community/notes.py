"""Community Notes statuses, decided by agreement across people with different rating histories.

This follows the approach X published for Community Notes (github.com/twitter/communitynotes): every rating is
modelled as

    rating ≈ μ + rater_intercept + note_intercept + rater_factor × note_factor

and fitted with regularisation (λ = 0.15 on intercepts, 0.03 on factors). The factor captures the perspective a rater
shares with others who rate the same way; the note intercept is the helpfulness that is left once that shared
perspective is explained. A note is shown as Helpful only when its intercept is high and its factor is small, which
means raters who usually disagree both found it helpful. A simple majority cannot make a note Helpful.

Statuses: Pending (too few ratings), Helpful, Not helpful, Disputed (raters split along their usual lines), and
Insufficient consensus (enough ratings, but neither agreement nor a clear split). Notes never change an infrastructure
score; they are shown beside the post.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CommunityNote, CommunityNoteRating, CommunityNoteStatusEvent

VALUES = {"helpful": 1.0, "somewhat": 0.5, "not_helpful": 0.0}
LAMBDA_INTERCEPT, LAMBDA_FACTOR = 0.15, 0.03
MIN_RATINGS = 5
HELPFUL_INTERCEPT = 0.40
NOT_HELPFUL_INTERCEPT = -0.05
FACTOR_LIMIT = 0.50
MIN_HELPFUL_PER_SIDE = 2  # helpful ratings needed from raters on each side of the perspective axis
STATUS_LABELS = {
    "pending": "Pending",
    "helpful": "Helpful",
    "not_helpful": "Not helpful",
    "disputed": "Disputed",
    "insufficient": "Insufficient consensus",
}


@dataclass
class Fit:
    mu: float
    rater_intercept: dict
    rater_factor: dict
    note_intercept: dict
    note_factor: dict


def fit(ratings: list[tuple], iterations: int = 60) -> Fit:
    """Alternating ridge regression for the one-factor model. ratings: (rater, note, value)."""
    raters = sorted({r for r, _, _ in ratings}, key=str)
    notes = sorted({n for _, n, _ in ratings}, key=str)
    ri = {r: i for i, r in enumerate(raters)}
    ni = {n: i for i, n in enumerate(notes)}
    u = np.array([ri[r] for r, _, _ in ratings], dtype=int)
    n = np.array([ni[k] for _, k, _ in ratings], dtype=int)
    y = np.array([v for _, _, v in ratings], dtype=float)
    rng = np.random.default_rng(7)  # fixed seed: the same ratings always give the same statuses
    bu, bn = np.zeros(len(raters)), np.zeros(len(notes))
    fu, fn = rng.normal(0, 0.1, len(raters)), rng.normal(0, 0.1, len(notes))
    mu = float(y.mean()) if len(y) else 0.0
    reg = np.diag([LAMBDA_INTERCEPT, LAMBDA_FACTOR])

    def solve(idx_self, idx_other, b_other, f_other, count):
        b, f = np.zeros(count), np.zeros(count)
        for k in range(count):
            sel = idx_self == k
            if not sel.any():
                continue
            x = np.stack([np.ones(sel.sum()), f_other[idx_other[sel]]], axis=1)
            t = y[sel] - mu - b_other[idx_other[sel]]
            b[k], f[k] = np.linalg.solve(x.T @ x + reg, x.T @ t)
        return b, f

    for _ in range(iterations):
        bu, fu = solve(u, n, bn, fn, len(raters))
        bn, fn = solve(n, u, bu, fu, len(notes))
        mu = float(np.mean(y - bu[u] - bn[n] - fu[u] * fn[n]))
    # Orient the factor axis consistently (largest rater group positive) so explanations do not flip sign.
    if (fu < 0).sum() > (fu > 0).sum():
        fu, fn = -fu, -fn
    return Fit(
        mu,
        {r: float(bu[ri[r]]) for r in raters},
        {r: float(fu[ri[r]]) for r in raters},
        {k: float(bn[ni[k]]) for k in notes},
        {k: float(fn[ni[k]]) for k in notes},
    )


def _signed(v: float) -> str:
    return "0.00" if abs(v) < 0.005 else f"{v:+.2f}"


def decide(note_id, ratings: list[tuple], f: Fit) -> tuple[str, str]:
    """Status and a plain explanation for one note, from its ratings and the fitted model."""
    mine = [(r, v) for r, k, v in ratings if k == note_id]
    if len(mine) < MIN_RATINGS:
        return (
            "pending",
            f"Rated by {len(mine)} contributor{'' if len(mine) == 1 else 's'}; a status needs at least {MIN_RATINGS}.",
        )
    b, fac = f.note_intercept[note_id], f.note_factor[note_id]
    side_a = [v for r, v in mine if f.rater_factor[r] >= 0]
    side_b = [v for r, v in mine if f.rater_factor[r] < 0]
    helpful_a = sum(1 for v in side_a if v >= 1.0)
    helpful_b = sum(1 for v in side_b if v >= 1.0)
    nums = f"helpfulness across perspectives {_signed(b)}, perspective lean {_signed(fac)}, {len(mine)} ratings"
    if (
        b >= HELPFUL_INTERCEPT
        and abs(fac) < FACTOR_LIMIT
        and helpful_a >= MIN_HELPFUL_PER_SIDE
        and helpful_b >= MIN_HELPFUL_PER_SIDE
    ):
        return "helpful", f"Contributors who usually rate differently both found this note helpful ({nums})."
    if b <= NOT_HELPFUL_INTERCEPT - 0.8 * abs(fac):
        return "not_helpful", f"Contributors across perspectives found this note not helpful ({nums})."
    if side_a and side_b and abs(fac) >= FACTOR_LIMIT and abs(np.mean(side_a) - np.mean(side_b)) >= 0.5:
        return (
            "disputed",
            f"Ratings split along contributors' usual lines: one group rated it {np.mean(side_a):.0%} helpful, the other {np.mean(side_b):.0%} ({nums}).",
        )
    return (
        "insufficient",
        f"Enough ratings, but no agreement across perspectives yet ({nums}). Helpful needs {HELPFUL_INTERCEPT:+.2f} or more with a lean under {FACTOR_LIMIT:.2f} and helpful ratings from both sides.",
    )


def recompute(db: Session) -> int:
    """Refit on every rating and update note statuses. Returns how many statuses changed."""
    rows = db.execute(select(CommunityNoteRating.rater_id, CommunityNoteRating.note_id, CommunityNoteRating.value)).all()
    ratings = [(r, n, VALUES[v]) for r, n, v in rows]
    f = fit(ratings) if ratings else Fit(0.0, {}, {}, {}, {})
    counts: dict[uuid.UUID, int] = {}
    for _, n, _ in ratings:
        counts[n] = counts.get(n, 0) + 1
    changed = 0
    now = datetime.now(UTC)
    for note in db.scalars(select(CommunityNote)).all():
        status, why = (
            decide(note.id, ratings, f)
            if note.id in counts
            else ("pending", f"No ratings yet; a status needs at least {MIN_RATINGS}.")
        )
        note.ratings_count = counts.get(note.id, 0)
        note.intercept = f.note_intercept.get(note.id)
        note.factor = f.note_factor.get(note.id)
        if status != note.status or note.status_changed_at is None:
            note.status = status
            note.status_changed_at = now
            db.add(
                CommunityNoteStatusEvent(
                    note_id=note.id,
                    status=status,
                    intercept=note.intercept,
                    factor=note.factor,
                    ratings_count=note.ratings_count,
                    explanation=why,
                )
            )
            changed += 1
    return changed


def explanation(db: Session, note: CommunityNote) -> str:
    ev = db.scalars(
        select(CommunityNoteStatusEvent)
        .where(CommunityNoteStatusEvent.note_id == note.id)
        .order_by(CommunityNoteStatusEvent.created_at.desc())
        .limit(1)
    ).first()
    return ev.explanation if ev else ""
