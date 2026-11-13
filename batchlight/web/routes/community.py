"""Community infrastructure pages: share photos, see what the vision models observed, add community notes,
discuss, and compare places category by category.

Separation is kept in the pages as in the data: the assessment panel, community notes, the discussion and official
records are separate sections, each labelled with where it comes from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ...community import categories as C
from ...community import notes as N
from ...community import projects as P
from ...community import rankings as R
from ...community import reps
from ...community.assess import (
    AREA_FULL,
    CALIBRATION,
    DETECT_THRESHOLD,
    ENGINE_VERSION,
    NOT_SCORED_FIT,
    PRIVACY_THRESHOLD,
    SEG_THRESHOLD,
)
from ...community.duplicates import DHASH_MAX, PHASH_MAX, REPEAT_HOURS, REPEAT_METRES, REPEAT_SIMILARITY
from ...community.models import (
    LOCALITY_KINDS,
    NOTE_KINDS,
    RATINGS,
    CommunityAssessment,
    CommunityComment,
    CommunityCommentVote,
    CommunityImage,
    CommunityLocality,
    CommunityNote,
    CommunityNoteRating,
    CommunityNoteStatusEvent,
    CommunityNoteVersion,
    CommunityPost,
    CommunityPostEdit,
    CommunityReport,
)
from ...community.service import PostInput, PostRejected, create_post, edit_post, validate
from ...community.storage import storage
from ...community.vision import models as vision_models
from ...config import get_settings
from ...infra.geo import BY_SLUG, REGIONS
from ...models import InfraProject, User
from ...security import hash_password, password_problem
from ...services import audit
from ..auth import CSRF, DB, OptionalAuth, UserAuth, client_ip, create_session, flash, safe_next
from ..templating import render
from .auth_routes import normalise_email

router = APIRouter(prefix="/community", include_in_schema=False)
PAGE = 24
MAX_DEPTH = 6
REPORT_REASONS = {
    "privacy": "Shows a person's face, a number plate or private details",
    "not_infrastructure": "Not public infrastructure",
    "misleading": "Misleading or wrong place",
    "abusive": "Abusive or hateful",
    "spam": "Spam or advertising",
    "other": "Something else",
}


def _redirect(url: str, message: str | None = None, level: str = "success") -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if message:
        flash(r, message, level)
    return r


def _visible(post: CommunityPost | None, auth) -> bool:
    if post is None:
        return False
    if post.status == "published":
        return True
    if auth and (auth.user.is_curator or post.author_id == auth.user.id):
        return post.status != "removed" or auth.user.is_curator
    return False


def _post_or_404(db, post_id: str, auth) -> CommunityPost:
    try:
        pid = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(404) from None
    post = db.get(CommunityPost, pid)
    if not _visible(post, auth):
        raise HTTPException(404)
    return post


def _current(db, post_id) -> CommunityAssessment | None:
    return db.scalar(
        select(CommunityAssessment).where(CommunityAssessment.post_id == post_id, CommunityAssessment.is_current.is_(True))
    )


def _counts(db, post_ids: list) -> dict:
    if not post_ids:
        return {}
    notes = dict(
        db.execute(
            select(CommunityNote.post_id, func.count())
            .where(CommunityNote.post_id.in_(post_ids), CommunityNote.hidden_at.is_(None))
            .group_by(CommunityNote.post_id)
        ).all()
    )
    helpful = dict(
        db.execute(
            select(CommunityNote.post_id, func.count())
            .where(CommunityNote.post_id.in_(post_ids), CommunityNote.status == "helpful", CommunityNote.hidden_at.is_(None))
            .group_by(CommunityNote.post_id)
        ).all()
    )
    comments = dict(
        db.execute(
            select(CommunityComment.post_id, func.count())
            .where(CommunityComment.post_id.in_(post_ids), CommunityComment.deleted_at.is_(None))
            .group_by(CommunityComment.post_id)
        ).all()
    )
    return {
        pid: {"notes": notes.get(pid, 0), "helpful": helpful.get(pid, 0), "comments": comments.get(pid, 0)} for pid in post_ids
    }


def _common() -> dict:
    return {
        "CATEGORIES": C.CATEGORIES,
        "CATEGORY_LABELS": C.LABELS,
        "REGIONS": REGIONS,
        "REGION_BY_SLUG": BY_SLUG,
        "STATUS_LABELS": N.STATUS_LABELS,
    }


# ------------------------------------------------------------------------------------------------ feed
@router.get("")
def feed(request: Request, db: DB, auth: OptionalAuth, category: str = "", state: str = "", page: int = Query(1, ge=1, le=1000)):
    q = (
        select(CommunityPost)
        .where(CommunityPost.status == "published")
        .options(selectinload(CommunityPost.locality), selectinload(CommunityPost.image))
    )
    if category in C.BY_SLUG:
        q = q.where(CommunityPost.category == category)
    if state in BY_SLUG:
        q = q.where(CommunityPost.state_slug == state)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    posts = db.scalars(
        q.order_by(CommunityPost.published_at.desc().nulls_last(), CommunityPost.created_at.desc())
        .offset((page - 1) * PAGE)
        .limit(PAGE)
    ).all()
    ids = [p.id for p in posts]
    assessments = (
        {
            a.post_id: a
            for a in db.scalars(
                select(CommunityAssessment).where(CommunityAssessment.post_id.in_(ids), CommunityAssessment.is_current.is_(True))
            ).all()
        }
        if ids
        else {}
    )
    mine = []
    if auth:
        mine = db.scalars(
            select(CommunityPost)
            .where(CommunityPost.author_id == auth.user.id, CommunityPost.status == "processing")
            .order_by(CommunityPost.created_at.desc())
            .limit(5)
        ).all()
    totals = {
        "posts": db.scalar(select(func.count()).select_from(CommunityPost).where(CommunityPost.status == "published")) or 0,
        "places": db.scalar(
            select(func.count(func.distinct(CommunityPost.locality_id))).where(CommunityPost.status == "published")
        )
        or 0,
        "notes": db.scalar(select(func.count()).select_from(CommunityNote).where(CommunityNote.hidden_at.is_(None))) or 0,
        "contributors": db.scalar(
            select(func.count(func.distinct(CommunityPost.author_id))).where(CommunityPost.status == "published")
        )
        or 0,
    }
    return render(
        request,
        "community/feed.html",
        {
            **_common(),
            "posts": posts,
            "assessments": assessments,
            "counts": _counts(db, ids),
            "category": category,
            "state": state,
            "page": page,
            "pages": max(1, (total + PAGE - 1) // PAGE),
            "total": total,
            "mine": mine,
            "totals": totals,
            "uploads_enabled": get_settings().uploads_enabled,
        },
    )


# ------------------------------------------------------------------------------------------------ join (account only)
@router.get("/join")
def join_form(request: Request, auth: OptionalAuth, next: str = "/community"):
    if auth:
        return RedirectResponse(safe_next(next, "/community"), status_code=303)
    return render(request, "community/join.html", {"form": {}, "errors": {}, "next": safe_next(next, "/community")})


@router.post("/join", dependencies=[CSRF])
def join(
    request: Request,
    db: DB,
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/community"),
):
    form = {"full_name": " ".join(full_name.split())[:200], "email": email.strip()}
    errors = {}
    if not 2 <= len(form["full_name"]) <= 200:
        errors["full_name"] = "Enter your name. It is shown with what you post."
    normalised = normalise_email(email)
    if not normalised:
        errors["email"] = "Enter a valid email address."
    problem = password_problem(password, normalised)
    if problem:
        errors["password"] = problem
    if not errors and db.scalar(select(User.id).where(User.email == normalised)):
        errors["email"] = (
            "This email can't be used to create an account. If you already have one, sign in or reset your password."
        )
    if errors:
        return render(
            request,
            "community/join.html",
            {"form": form, "errors": errors, "next": safe_next(next, "/community")},
            status_code=422,
        )
    user = User(email=normalised, full_name=form["full_name"], password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit.record(db, "user_signed_up", user_id=user.id, subject=normalised, ip=client_ip(request), via="community")
    response = RedirectResponse(safe_next(next, "/community"), status_code=303)
    create_session(db, response, request, user, None)
    db.commit()
    flash(response, "Welcome. You can now share photos, add notes and join discussions.")
    return response


# ------------------------------------------------------------------------------------------------ new post
def _form_context(form: dict | None = None, errors: dict | None = None, duplicate_of=None) -> dict:
    return {
        **_common(),
        "form": form or {"locality_kind": "town"},
        "errors": errors or {},
        "LOCALITY_KINDS": LOCALITY_KINDS,
        "duplicate_of": duplicate_of,
        "uploads_enabled": get_settings().uploads_enabled,
    }


@router.get("/new")
def new_form(request: Request, auth: UserAuth, category: str = ""):
    return render(
        request,
        "community/new.html",
        _form_context({"locality_kind": "town", "category": category if category in C.BY_SLUG else ""}),
    )


@router.post("/new", dependencies=[CSRF])
async def new_post(request: Request, db: DB, auth: UserAuth, photo: UploadFile = File(None)):
    if not get_settings().uploads_enabled:
        raise HTTPException(403, "Photo uploads are not open yet.")
    form = await request.form()
    values = {
        k: str(form.get(k, "")).strip()
        for k in (
            "category",
            "title",
            "body",
            "latitude",
            "longitude",
            "location_source",
            "state_slug",
            "district",
            "locality_kind",
            "locality_name",
            "official_reference",
        )
    }

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None

    data = PostInput(
        values["category"],
        values["title"],
        values["body"],
        num(values["latitude"]),
        num(values["longitude"]),
        values["location_source"] or "pin",
        values["state_slug"],
        values["district"],
        values["locality_kind"],
        values["locality_name"],
        values["official_reference"],
    )
    errors = validate(data)
    raw = await photo.read(13 * 1024 * 1024) if photo is not None and photo.filename else b""
    if not raw:
        errors["photo"] = "Choose a photo to share."
    if form.get("consent") != "yes":
        errors["consent"] = "Please confirm the photo is yours to share and shows public infrastructure."
    if errors:
        return render(request, "community/new.html", _form_context(values, errors), status_code=422)
    try:
        post = create_post(db, auth.user.id, data, raw)
    except PostRejected as exc:
        db.rollback()
        return render(
            request,
            "community/new.html",
            _form_context(values, {exc.field: str(exc)}, exc.duplicate_of),
            status_code=409 if exc.duplicate_of else 422,
        )
    audit.record(db, "community_post_created", user_id=auth.user.id, entity_type="community_post", entity_id=post.id)
    db.commit()
    return _redirect(
        f"/community/posts/{post.id}",
        "Thank you. Your photo is being checked: faces and number plates are blurred and the image is assessed before it goes public.",
    )


# ------------------------------------------------------------------------------------------------ post page
def _comment_tree(comments: list[CommunityComment], sort: str) -> list[dict]:
    children: dict = {}
    for c in comments:
        children.setdefault(c.parent_id, []).append(c)
    key = (lambda c: (-c.score, c.created_at)) if sort == "top" else (lambda c: c.created_at)

    def build(parent):
        return [{"c": c, "replies": build(c.id)} for c in sorted(children.get(parent, []), key=key)]

    return build(None)


@router.get("/posts/{post_id}")
def post_page(request: Request, post_id: str, db: DB, auth: OptionalAuth, sort: str = "top"):
    post = _post_or_404(db, post_id, auth)
    image = db.scalar(select(CommunityImage).where(CommunityImage.post_id == post.id))
    assessment = _current(db, post.id)
    notes = db.scalars(
        select(CommunityNote)
        .where(CommunityNote.post_id == post.id, CommunityNote.hidden_at.is_(None))
        .order_by(CommunityNote.created_at)
    ).all()
    order = {"helpful": 0, "disputed": 1, "insufficient": 2, "pending": 3, "not_helpful": 4}
    notes = sorted(notes, key=lambda n: (order.get(n.status, 9), n.created_at))
    my_ratings = {}
    if auth and notes:
        my_ratings = dict(
            db.execute(
                select(CommunityNoteRating.note_id, CommunityNoteRating.value).where(
                    CommunityNoteRating.rater_id == auth.user.id, CommunityNoteRating.note_id.in_([n.id for n in notes])
                )
            ).all()
        )
    rating_counts = {}
    for nid, value, cnt in db.execute(
        select(CommunityNoteRating.note_id, CommunityNoteRating.value, func.count())
        .where(CommunityNoteRating.note_id.in_([n.id for n in notes] or [uuid.uuid4()]))
        .group_by(CommunityNoteRating.note_id, CommunityNoteRating.value)
    ).all():
        rating_counts.setdefault(nid, {})[value] = cnt
    events = {}
    for ev in db.scalars(
        select(CommunityNoteStatusEvent)
        .where(CommunityNoteStatusEvent.note_id.in_([n.id for n in notes] or [uuid.uuid4()]))
        .order_by(CommunityNoteStatusEvent.created_at)
    ).all():
        events.setdefault(ev.note_id, []).append(ev)
    versions = {}
    for v in db.scalars(
        select(CommunityNoteVersion)
        .where(CommunityNoteVersion.note_id.in_([n.id for n in notes] or [uuid.uuid4()]))
        .order_by(CommunityNoteVersion.created_at)
    ).all():
        versions.setdefault(v.note_id, []).append(v)
    comments = db.scalars(
        select(CommunityComment).where(CommunityComment.post_id == post.id).options(selectinload(CommunityComment.author))
    ).all()
    my_votes = {}
    if auth and comments:
        my_votes = dict(
            db.execute(
                select(CommunityCommentVote.comment_id, CommunityCommentVote.value).where(
                    CommunityCommentVote.user_id == auth.user.id, CommunityCommentVote.comment_id.in_([c.id for c in comments])
                )
            ).all()
        )
    edits = db.scalars(
        select(CommunityPostEdit).where(CommunityPostEdit.post_id == post.id).order_by(CommunityPostEdit.created_at)
    ).all()
    links = P.links_for(db, post.id)
    linked = [(link, db.get(InfraProject, link.project_id)) for link in links]
    finance = {project.id: P.finance_facts(db, project) for link, project in linked if link.status == "linked" and project}
    related = P.might_be_related(db, post) if not any(link.status == "linked" for link in links) else []
    return render(
        request,
        "community/post.html",
        {
            **_common(),
            "post": post,
            "category": C.get(post.category),
            "image": image,
            "assessment": assessment,
            "notes": notes,
            "my_ratings": my_ratings,
            "rating_counts": rating_counts,
            "events": events,
            "versions": versions,
            "RATINGS": RATINGS,
            "NOTE_KINDS": NOTE_KINDS,
            "tree": _comment_tree(list(comments), sort),
            "comment_count": sum(1 for c in comments if c.deleted_at is None),
            "my_votes": my_votes,
            "sort": sort,
            "MAX_DEPTH": MAX_DEPTH,
            "edits": edits,
            "representatives": reps.for_post(db, post.id),
            "NOT_AVAILABLE": reps.NOT_AVAILABLE,
            "REP_LEVELS": reps.LEVELS,
            "linked": linked,
            "finance": finance,
            "related": related,
            "CAVEAT": P.CAVEAT,
            "REPORT_REASONS": REPORT_REASONS,
            "is_author": bool(auth and post.author_id == auth.user.id),
            "share_url": f"{get_settings().base_url}/community/posts/{post.id}",
            "DETECT_THRESHOLD": DETECT_THRESHOLD,
        },
    )


@router.get("/media/{post_id}/{kind}")
def media(post_id: str, kind: str, db: DB, auth: OptionalAuth):
    post = _post_or_404(db, post_id, auth)
    image = db.scalar(select(CommunityImage).where(CommunityImage.post_id == post.id))
    if image is None:
        raise HTTPException(404)
    if post.status == "processing" and not (auth and auth.user.is_curator):
        # Before faces and plates are blurred, only the person who posted it may see the photo.
        if not (auth and post.author_id == auth.user.id):
            raise HTTPException(404)
    if kind == "photo":
        key, mime = image.storage_key, image.mime
    elif kind == "thumb":
        key, mime = image.thumb_key, "image/jpeg"
    elif kind == "mask":
        a = _current(db, post.id)
        if not a or not a.mask_key:
            raise HTTPException(404)
        key, mime = a.mask_key, "image/png"
    else:
        raise HTTPException(404)
    try:
        data = storage().get(key)
    except FileNotFoundError:
        raise HTTPException(404) from None
    cache = "public, max-age=3600" if post.status == "published" else "private, no-store"
    return Response(data, media_type=mime, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})


@router.post("/posts/{post_id}/share", dependencies=[CSRF])
def share(post_id: str, db: DB, auth: OptionalAuth):
    post = _post_or_404(db, post_id, auth)
    if post.status == "published":
        post.share_count += 1
        db.commit()
    return JSONResponse({"shares": post.share_count})


@router.get("/posts/{post_id}/edit")
def edit_form(request: Request, post_id: str, db: DB, auth: UserAuth):
    post = _post_or_404(db, post_id, auth)
    if post.author_id != auth.user.id:
        raise HTTPException(403)
    return render(request, "community/edit.html", {**_common(), "post": post, "errors": {}})


@router.post("/posts/{post_id}/edit", dependencies=[CSRF])
def edit(
    request: Request,
    post_id: str,
    db: DB,
    auth: UserAuth,
    title: str = Form(""),
    body: str = Form(""),
    official_reference: str = Form(""),
):
    post = _post_or_404(db, post_id, auth)
    if post.author_id != auth.user.id:
        raise HTTPException(403)
    try:
        changed = edit_post(db, post, auth.user.id, title, body, official_reference)
    except PostRejected as exc:
        return render(
            request, "community/edit.html", {**_common(), "post": post, "errors": {exc.field: str(exc)}}, status_code=422
        )
    if "official_reference" in changed and post.status == "published":
        P.link(db, post, auth.user.id)
    db.commit()
    return _redirect(
        f"/community/posts/{post.id}", "Saved. The earlier text stays in the edit history." if changed else "Nothing changed."
    )


@router.post("/posts/{post_id}/report", dependencies=[CSRF])
def report(
    post_id: str,
    db: DB,
    auth: UserAuth,
    target_type: str = Form("post"),
    target_id: str = Form(""),
    reason: str = Form(""),
    details: str = Form(""),
):
    post = _post_or_404(db, post_id, auth)
    if reason not in REPORT_REASONS or target_type not in ("post", "comment", "note"):
        raise HTTPException(400)
    try:
        tid = uuid.UUID(target_id) if target_id else post.id
    except ValueError:
        raise HTTPException(400) from None
    db.add(
        CommunityReport(
            target_type=target_type, target_id=tid, reporter_id=auth.user.id, reason=reason, details=details.strip()[:1000]
        )
    )
    db.commit()
    return _redirect(f"/community/posts/{post.id}", "Thank you. A moderator will look at it.", "info")


# ------------------------------------------------------------------------------------------------ community notes
def _notes_today(db, user_id) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    return (
        db.scalar(
            select(func.count())
            .select_from(CommunityNote)
            .where(CommunityNote.author_id == user_id, CommunityNote.created_at >= since)
        )
        or 0
    )


def _sources(raw: str) -> list[str]:
    out = []
    for line in raw.replace(",", "\n").splitlines():
        s = line.strip()
        if s.startswith(("https://", "http://")) and len(s) <= 500 and " " not in s:
            out.append(s)
    return out[:5]


@router.post("/posts/{post_id}/notes", dependencies=[CSRF])
def add_note(post_id: str, db: DB, auth: UserAuth, kind: str = Form("context"), body: str = Form(""), sources: str = Form("")):
    post = _post_or_404(db, post_id, auth)
    body = body.strip()
    if kind not in NOTE_KINDS or not 20 <= len(body) <= 1500:
        return _redirect(
            f"/community/posts/{post.id}#notes",
            "A note needs 20 to 1,500 characters of context, correction or evidence.",
            "error",
        )
    if _notes_today(db, auth.user.id) >= get_settings().community_notes_per_day:
        return _redirect(f"/community/posts/{post.id}#notes", "You have added the most notes allowed in one day.", "error")
    note = CommunityNote(post_id=post.id, author_id=auth.user.id, kind=kind, body=body, sources=_sources(sources))
    db.add(note)
    db.flush()
    N.recompute(db)
    db.commit()
    return _redirect(
        f"/community/posts/{post.id}#note-{note.id}",
        "Note added. It shows as Pending until contributors with different rating histories have rated it.",
    )


@router.post("/notes/{note_id}/rate", dependencies=[CSRF])
def rate_note(note_id: str, db: DB, auth: UserAuth, value: str = Form(""), next: str = Form("")):
    try:
        note = db.get(CommunityNote, uuid.UUID(note_id))
    except ValueError:
        raise HTTPException(404) from None
    if note is None or note.hidden_at is not None or value not in RATINGS:
        raise HTTPException(404)
    back = safe_next(next, f"/community/posts/{note.post_id}#note-{note.id}")
    if note.author_id == auth.user.id:
        return _redirect(back, "You can't rate your own note.", "error")
    existing = db.scalar(
        select(CommunityNoteRating).where(CommunityNoteRating.note_id == note.id, CommunityNoteRating.rater_id == auth.user.id)
    )
    if existing:
        existing.value = value
        existing.updated_at = datetime.now(UTC)
    else:
        db.add(CommunityNoteRating(note_id=note.id, rater_id=auth.user.id, value=value))
    db.flush()
    N.recompute(db)
    db.commit()
    return _redirect(back, "Thanks for rating. Statuses are recalculated from everyone's ratings.")


@router.post("/notes/{note_id}/edit", dependencies=[CSRF])
def edit_note(note_id: str, db: DB, auth: UserAuth, body: str = Form(""), sources: str = Form("")):
    note = db.get(CommunityNote, uuid.UUID(note_id))
    if note is None or note.author_id != auth.user.id:
        raise HTTPException(404)
    body = body.strip()
    if not 20 <= len(body) <= 1500:
        return _redirect(f"/community/posts/{note.post_id}#note-{note.id}", "A note needs 20 to 1,500 characters.", "error")
    db.add(CommunityNoteVersion(note_id=note.id, body=note.body, sources=note.sources))
    note.body, note.sources, note.edited_at = body, _sources(sources), datetime.now(UTC)
    # An edited note is rated afresh: earlier ratings were about different text.
    db.query(CommunityNoteRating).filter(CommunityNoteRating.note_id == note.id).delete()
    N.recompute(db)
    db.commit()
    return _redirect(
        f"/community/posts/{note.post_id}#note-{note.id}",
        "Note updated. Earlier ratings were cleared because they were for the old text.",
    )


@router.get("/notes")
def notes_queue(request: Request, db: DB, auth: OptionalAuth, status: str = "pending"):
    q = (
        select(CommunityNote)
        .join(CommunityPost, CommunityPost.id == CommunityNote.post_id)
        .where(CommunityPost.status == "published", CommunityNote.hidden_at.is_(None))
        .options(selectinload(CommunityNote.author))
    )
    if status in N.STATUS_LABELS:
        q = q.where(CommunityNote.status == status)
    notes = db.scalars(q.order_by(CommunityNote.created_at.desc()).limit(50)).all()
    posts = {
        p.id: p
        for p in db.scalars(select(CommunityPost).where(CommunityPost.id.in_([n.post_id for n in notes] or [uuid.uuid4()]))).all()
    }
    mine = {}
    if auth and notes:
        mine = dict(
            db.execute(
                select(CommunityNoteRating.note_id, CommunityNoteRating.value).where(
                    CommunityNoteRating.rater_id == auth.user.id, CommunityNoteRating.note_id.in_([n.id for n in notes])
                )
            ).all()
        )
    counts = dict(
        db.execute(
            select(CommunityNote.status, func.count()).where(CommunityNote.hidden_at.is_(None)).group_by(CommunityNote.status)
        ).all()
    )
    return render(
        request,
        "community/notes.html",
        {
            **_common(),
            "notes": notes,
            "posts": posts,
            "status": status,
            "mine": mine,
            "counts": counts,
            "RATINGS": RATINGS,
            "MIN_RATINGS": N.MIN_RATINGS,
        },
    )


# ------------------------------------------------------------------------------------------------ discussions
@router.post("/posts/{post_id}/comments", dependencies=[CSRF])
def add_comment(post_id: str, db: DB, auth: UserAuth, body: str = Form(""), parent_id: str = Form("")):
    post = _post_or_404(db, post_id, auth)
    body = body.strip()
    if not 2 <= len(body) <= 5000:
        return _redirect(f"/community/posts/{post.id}#discussion", "A comment needs 2 to 5,000 characters.", "error")
    since = datetime.now(UTC) - timedelta(hours=1)
    if (
        db.scalar(
            select(func.count())
            .select_from(CommunityComment)
            .where(CommunityComment.author_id == auth.user.id, CommunityComment.created_at >= since)
        )
        or 0
    ) >= get_settings().community_comments_per_hour:
        return _redirect(f"/community/posts/{post.id}#discussion", "You are commenting very fast. Please wait a little.", "error")
    parent = None
    if parent_id:
        try:
            parent = db.get(CommunityComment, uuid.UUID(parent_id))
        except ValueError:
            parent = None
        if parent is None or parent.post_id != post.id:
            raise HTTPException(400)
    depth = min(MAX_DEPTH, parent.depth + 1) if parent else 0
    c = CommunityComment(post_id=post.id, parent_id=parent.id if parent else None, author_id=auth.user.id, body=body, depth=depth)
    db.add(c)
    db.commit()
    return _redirect(f"/community/posts/{post.id}?open=discussion#comment-{c.id}")


@router.post("/comments/{comment_id}/vote", dependencies=[CSRF])
def vote(comment_id: str, db: DB, auth: UserAuth, value: int = Form(0)):
    c = db.get(CommunityComment, uuid.UUID(comment_id))
    if c is None or c.deleted_at is not None or value not in (-1, 1):
        raise HTTPException(404)
    existing = db.get(CommunityCommentVote, (c.id, auth.user.id))
    if existing and existing.value == value:
        db.delete(existing)  # pressing the same arrow again takes the vote back
    elif existing:
        existing.value = value
    else:
        db.add(CommunityCommentVote(comment_id=c.id, user_id=auth.user.id, value=value))
    db.flush()
    c.score = (
        db.scalar(select(func.coalesce(func.sum(CommunityCommentVote.value), 0)).where(CommunityCommentVote.comment_id == c.id))
        or 0
    )
    db.commit()
    return _redirect(f"/community/posts/{c.post_id}?open=discussion#comment-{c.id}")


@router.post("/comments/{comment_id}/edit", dependencies=[CSRF])
def edit_comment(comment_id: str, db: DB, auth: UserAuth, body: str = Form("")):
    c = db.get(CommunityComment, uuid.UUID(comment_id))
    if c is None or c.author_id != auth.user.id or c.deleted_at is not None or not 2 <= len(body.strip()) <= 5000:
        raise HTTPException(404)
    c.body, c.edited_at = body.strip(), datetime.now(UTC)
    db.commit()
    return _redirect(f"/community/posts/{c.post_id}?open=discussion#comment-{c.id}")


@router.post("/comments/{comment_id}/delete", dependencies=[CSRF])
def delete_comment(comment_id: str, db: DB, auth: UserAuth):
    c = db.get(CommunityComment, uuid.UUID(comment_id))
    if c is None or (c.author_id != auth.user.id and not auth.user.is_curator):
        raise HTTPException(404)
    c.deleted_at = datetime.now(UTC)  # replies stay; the text is replaced by "deleted"
    db.commit()
    return _redirect(f"/community/posts/{c.post_id}?open=discussion#comment-{c.id}")


# ------------------------------------------------------------------------------------------------ rankings and places
@router.get("/rankings")
def rankings(request: Request, db: DB, auth: OptionalAuth, category: str = "roads", level: str = "state", state: str = ""):
    if category not in C.BY_SLUG:
        category = "roads"
    if level not in R.LEVELS:
        level = "state"
    state = state if state in BY_SLUG else ""
    items = R.units(db, category, level, state or None)
    return render(
        request,
        "community/rankings.html",
        {
            **_common(),
            "category": C.get(category),
            "level": level,
            "state": state,
            "items": items,
            "LEVELS": R.LEVELS,
            "LEVEL_LABELS": R.LEVEL_LABELS,
            "R": R,
            "MIN_RANK": R.MIN_RANK,
            "MIN_CONTRIBUTORS": R.MIN_CONTRIBUTORS,
            "spark": R.sparkline,
        },
    )


@router.get("/compare")
def compare(
    request: Request,
    db: DB,
    auth: OptionalAuth,
    category: str = "roads",
    level: str = "state",
    key: list[str] = Query(default=[]),
):
    if category not in C.BY_SLUG or level not in R.LEVELS:
        raise HTTPException(404)
    keys = key[:4]
    try:
        items = R.units(db, category, level, None, keys) if keys else []
    except ValueError:
        raise HTTPException(404) from None
    months = sorted({m for u in items for m, _, _ in u.trend})
    return render(
        request,
        "community/compare.html",
        {
            **_common(),
            "category": C.get(category),
            "level": level,
            "items": items,
            "months": months,
            "LEVEL_LABELS": R.LEVEL_LABELS,
            "R": R,
        },
    )


@router.get("/places/{level}/{key}")
def place(request: Request, level: str, key: str, db: DB, auth: OptionalAuth):
    if level not in R.LEVELS:
        raise HTTPException(404)
    if level in ("village", "town", "city"):
        try:
            loc = db.get(CommunityLocality, uuid.UUID(key))
        except ValueError:
            loc = None
        if loc is None or loc.kind != level:
            raise HTTPException(404)
        name, where = loc.name, (f"{loc.district}, " if loc.district else "") + BY_SLUG[loc.state_slug].name
        post_filter = CommunityPost.locality_id == loc.id
    elif level == "state":
        if key not in BY_SLUG:
            raise HTTPException(404)
        name, where, post_filter = BY_SLUG[key].name, "India", CommunityPost.state_slug == key
    else:
        if key not in R.COUNTRIES:
            raise HTTPException(404)
        name, where, post_filter = R.COUNTRIES[key], "", CommunityPost.country_code == key
    by_category = []
    for cat in C.CATEGORIES:
        found = R.units(db, cat.slug, level, None, [key])
        by_category.append((cat, found[0] if found else None))
    posts = db.scalars(
        select(CommunityPost)
        .where(post_filter, CommunityPost.status == "published")
        .options(selectinload(CommunityPost.image), selectinload(CommunityPost.locality))
        .order_by(CommunityPost.published_at.desc())
        .limit(12)
    ).all()
    return render(
        request,
        "community/place.html",
        {
            **_common(),
            "level": level,
            "key": key,
            "name": name,
            "where": where,
            "by_category": by_category,
            "posts": posts,
            "spark": R.sparkline,
            "LEVEL_LABELS": R.LEVEL_LABELS,
            "R": R,
            "MIN_RANK": R.MIN_RANK,
        },
    )


@router.get("/contributors/{user_id}")
def contributor(request: Request, user_id: str, db: DB, auth: OptionalAuth):
    try:
        user = db.get(User, uuid.UUID(user_id))
    except ValueError:
        user = None
    if user is None:
        raise HTTPException(404)
    posts = db.scalars(
        select(CommunityPost)
        .where(CommunityPost.author_id == user.id, CommunityPost.status == "published")
        .options(selectinload(CommunityPost.image), selectinload(CommunityPost.locality))
        .order_by(CommunityPost.published_at.desc())
        .limit(24)
    ).all()
    notes = db.scalars(
        select(CommunityNote)
        .where(CommunityNote.author_id == user.id, CommunityNote.hidden_at.is_(None))
        .order_by(CommunityNote.created_at.desc())
        .limit(50)
    ).all()
    note_counts = {}
    for n in notes:
        note_counts[n.status] = note_counts.get(n.status, 0) + 1
    ratings = db.scalar(select(func.count()).select_from(CommunityNoteRating).where(CommunityNoteRating.rater_id == user.id)) or 0
    comments = (
        db.scalar(
            select(func.count())
            .select_from(CommunityComment)
            .where(CommunityComment.author_id == user.id, CommunityComment.deleted_at.is_(None))
        )
        or 0
    )
    return render(
        request,
        "community/contributor.html",
        {
            **_common(),
            "person": user,
            "posts": posts,
            "notes": notes,
            "note_counts": note_counts,
            "ratings": ratings,
            "comments": comments,
        },
    )


@router.get("/about")
def about(request: Request, db: DB, auth: OptionalAuth):
    return render(
        request,
        "community/about.html",
        {
            **_common(),
            "MODELS": vision_models.MODELS,
            "ENGINE_VERSION": ENGINE_VERSION,
            "DETECT_THRESHOLD": DETECT_THRESHOLD,
            "AREA_FULL": AREA_FULL,
            "NOT_SCORED_FIT": NOT_SCORED_FIT,
            "SEG_THRESHOLD": SEG_THRESHOLD,
            "PRIVACY_THRESHOLD": PRIVACY_THRESHOLD,
            "PRIVACY_QUERIES": C.PRIVACY_QUERIES,
            "CALIBRATION": CALIBRATION,
            "PHASH_MAX": PHASH_MAX,
            "DHASH_MAX": DHASH_MAX,
            "REPEAT_HOURS": REPEAT_HOURS,
            "REPEAT_METRES": REPEAT_METRES,
            "REPEAT_SIMILARITY": REPEAT_SIMILARITY,
            "N": N,
            "R": R,
            "REPS": reps,
        },
    )


# ------------------------------------------------------------------------------------------------ moderation
@router.get("/moderation")
def moderation(request: Request, db: DB, auth: UserAuth):
    if not auth.user.is_curator:
        raise HTTPException(403)
    reports = db.scalars(
        select(CommunityReport).where(CommunityReport.status == "open").order_by(CommunityReport.created_at).limit(100)
    ).all()
    return render(request, "community/moderation.html", {**_common(), "reports": reports, "REPORT_REASONS": REPORT_REASONS})


@router.post("/moderation/{report_id}", dependencies=[CSRF])
def moderate(report_id: str, db: DB, auth: UserAuth, action: str = Form("")):
    if not auth.user.is_curator:
        raise HTTPException(403)
    rep = db.get(CommunityReport, uuid.UUID(report_id))
    if rep is None or action not in ("hide", "dismiss"):
        raise HTTPException(404)
    now = datetime.now(UTC)
    if action == "hide":
        if rep.target_type == "post" and (p := db.get(CommunityPost, rep.target_id)):
            p.status = "hidden"
        elif rep.target_type == "comment" and (c := db.get(CommunityComment, rep.target_id)):
            c.deleted_at = now
        elif rep.target_type == "note" and (n := db.get(CommunityNote, rep.target_id)):
            n.hidden_at = now
    rep.status, rep.resolved_at = ("actioned" if action == "hide" else "dismissed"), now
    audit.record(
        db, "community_moderated", user_id=auth.user.id, entity_type=rep.target_type, entity_id=rep.target_id, decision=action
    )
    db.commit()
    return _redirect("/community/moderation", "Done.")
