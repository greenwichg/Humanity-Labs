"""Signed-in workspace: dashboard, inbox, inventory, team, settings and API keys."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload

from ...config import get_settings
from ...models import (
    ORG_KINDS,
    OUTCOMES,
    ROLES,
    Alert,
    ApiKey,
    Holding,
    Invitation,
    Match,
    Membership,
    Notification,
    Organization,
    Session_,
    User,
)
from ...security import hash_password, new_api_key, new_token, password_problem, token_hash, verify_password
from ...services import audit, metrics
from ...services.alerts import first_lots, sources_status
from ...services.email import send_email
from ...services.holdings import (
    CSV_TEMPLATE,
    HoldingInput,
    archive_holding,
    create_holding,
    export_csv,
    import_rows,
    parse_csv,
    update_holding,
)
from ..auth import CSRF, DB, ManagerAuth, OrgAuth, UserAuth, client_ip, flash
from ..countries import COUNTRIES, valid_country
from ..templating import render
from .auth_routes import normalise_email

router = APIRouter(prefix="/app", include_in_schema=False)
F = Annotated[str, Form()]
PER_PAGE = 25
SEVERITY_SORT = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "info": 4}


def _redirect(url: str, message: str | None = None, level: str = "success") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if message:
        flash(response, message, level)
    return response


# --------------------------------------------------------------------------- dashboard


@router.get("")
def dashboard(request: Request, db: DB, auth: OrgAuth):
    org_id = auth.org.id
    open_matches = db.scalars(
        select(Match)
        .where(Match.org_id == org_id, Match.status == "open")
        .options(selectinload(Match.alert), selectinload(Match.holding))
    ).all()
    open_matches = sorted(open_matches, key=lambda m: (SEVERITY_SORT.get(m.alert.severity, 9), -m.confidence, m.created_at))
    holdings = (
        db.scalar(select(func.count()).select_from(Holding).where(Holding.org_id == org_id, Holding.archived_at.is_(None))) or 0
    )
    without_batch = (
        db.scalar(
            select(func.count())
            .select_from(Holding)
            .where(Holding.org_id == org_id, Holding.archived_at.is_(None), Holding.lot_key.is_(None))
        )
        or 0
    )
    return render(
        request,
        "app/dashboard.html",
        {
            "open_matches": open_matches[:8],
            "open_count": len(open_matches),
            "holdings": holdings,
            "without_batch": without_batch,
            "stats": metrics.match_metrics(db, org_id),
            "sources": sources_status(db),
        },
    )


# --------------------------------------------------------------------------- matches


@router.get("/matches")
def matches(request: Request, db: DB, auth: OrgAuth, status: str = "open", page: int = Query(1, ge=1, le=10_000)):
    status = status if status in ("open", "resolved", "all") else "open"
    stmt = select(Match).where(Match.org_id == auth.org.id)
    if status != "all":
        stmt = stmt.where(Match.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.join(Alert)
        .order_by(
            (Match.status == "resolved"),
            func.array_position(["critical", "high", "moderate", "low", "info"], Alert.severity),
            Match.created_at.desc(),
        )
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
        .options(selectinload(Match.alert), selectinload(Match.holding))
    ).all()
    return render(
        request,
        "app/matches.html",
        {"matches": rows, "status": status, "total": total, "page": page, "pages": max(1, -(-total // PER_PAGE))},
    )


def _own_match(db, auth, match_id: uuid.UUID) -> Match:
    match = db.scalar(
        select(Match)
        .where(Match.id == match_id, Match.org_id == auth.org.id)
        .options(selectinload(Match.alert).selectinload(Alert.source), selectinload(Match.holding), selectinload(Match.resolver))
    )
    if match is None:
        raise HTTPException(404)
    return match


@router.get("/matches/{match_id}")
def match_detail(request: Request, match_id: uuid.UUID, db: DB, auth: OrgAuth):
    match = _own_match(db, auth, match_id)
    db.execute(
        update(Notification)
        .where(Notification.match_id == match.id, Notification.user_id == auth.user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return render(
        request,
        "app/match_detail.html",
        {"match": match, "outcomes": OUTCOMES, "errors": {}, "lots": first_lots(db, [match.alert_id], 6)[match.alert_id]},
    )


@router.post("/matches/{match_id}/resolve", dependencies=[CSRF])
def resolve_match(
    request: Request, match_id: uuid.UUID, db: DB, auth: OrgAuth, outcome: F = "", units_affected: F = "", note: F = ""
):
    match = _own_match(db, auth, match_id)
    errors = {}
    if outcome not in OUTCOMES:
        errors["outcome"] = "Choose what you did."
    units = None
    if units_affected.strip():
        if not units_affected.strip().isdigit() or int(units_affected) > 1_000_000_000:
            errors["units_affected"] = "Enter a whole number of units, or leave it blank."
        else:
            units = int(units_affected)
    if len(note) > 2000:
        errors["note"] = "Keep the note under 2,000 characters."
    if errors:
        return render(
            request,
            "app/match_detail.html",
            {
                "match": match,
                "outcomes": OUTCOMES,
                "errors": errors,
                "lots": first_lots(db, [match.alert_id], 6)[match.alert_id],
                "form": {"outcome": outcome, "units_affected": units_affected, "note": note},
            },
            status_code=422,
        )
    match.status, match.outcome, match.units_affected = "resolved", outcome, units
    match.note = note.strip() or None
    match.resolved_at, match.resolved_by = datetime.now(UTC), auth.user.id
    audit.record(
        db,
        "match_resolved",
        org_id=auth.org.id,
        user_id=auth.user.id,
        entity_type="match",
        entity_id=match.id,
        outcome=outcome,
        units=units,
    )
    db.commit()
    return _redirect("/app/matches", "Recorded. Thank you for acting on this alert.")


@router.post("/matches/{match_id}/reopen", dependencies=[CSRF])
def reopen_match(match_id: uuid.UUID, db: DB, auth: OrgAuth):
    match = _own_match(db, auth, match_id)
    match.status, match.outcome, match.units_affected, match.resolved_at, match.resolved_by = "open", None, None, None, None
    audit.record(db, "match_reopened", org_id=auth.org.id, user_id=auth.user.id, entity_type="match", entity_id=match.id)
    db.commit()
    return _redirect(f"/app/matches/{match.id}", "Match reopened.", "info")


# --------------------------------------------------------------------------- inventory


@router.get("/inventory")
def inventory(request: Request, db: DB, auth: OrgAuth, q: str = "", page: int = Query(1, ge=1, le=10_000)):
    stmt = select(Holding).where(Holding.org_id == auth.org.id, Holding.archived_at.is_(None))
    q = q.strip()[:100]
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Holding.product_name.ilike(like),
                Holding.active_ingredient.ilike(like),
                Holding.lot.ilike(like),
                Holding.manufacturer.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Holding.product_name, Holding.created_at).offset((page - 1) * PER_PAGE).limit(PER_PAGE)).all()
    open_by_holding = dict(
        db.execute(
            select(Match.holding_id, func.count())
            .where(Match.org_id == auth.org.id, Match.status == "open", Match.holding_id.in_([h.id for h in rows]))
            .group_by(Match.holding_id)
        ).all()
    )
    return render(
        request,
        "app/inventory.html",
        {
            "holdings": rows,
            "q": q,
            "total": total,
            "page": page,
            "pages": max(1, -(-total // PER_PAGE)),
            "open_by_holding": open_by_holding,
        },
    )


FORM_FIELDS = (
    "product_name",
    "active_ingredient",
    "strength",
    "dosage_form",
    "manufacturer",
    "product_code",
    "lot",
    "expiry_date",
    "quantity",
    "location",
    "notes",
)


def _form_errors(exc: ValidationError) -> dict[str, str]:
    errors = {}
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "form"
        errors.setdefault(field, error["msg"].removeprefix("Value error, "))
    return errors


async def _read_holding_form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {name: str(form.get(name, "")) for name in FORM_FIELDS}


@router.get("/inventory/new")
def new_holding(request: Request, auth: OrgAuth, welcome: int = 0):
    return render(request, "app/holding_form.html", {"holding": None, "form": {}, "errors": {}, "welcome": bool(welcome)})


@router.post("/inventory", dependencies=[CSRF])
async def create(request: Request, db: DB, auth: OrgAuth):
    values = await _read_holding_form(request)
    try:
        data = HoldingInput(**values)
    except ValidationError as exc:
        return render(
            request,
            "app/holding_form.html",
            {"holding": None, "form": values, "errors": _form_errors(exc), "welcome": False},
            status_code=422,
        )
    holding, new_matches = create_holding(db, auth.org.id, data, auth.user.id)
    audit.record(db, "holding_created", org_id=auth.org.id, user_id=auth.user.id, entity_type="holding", entity_id=holding.id)
    db.commit()
    if new_matches:
        return _redirect(
            "/app/matches", f"{holding.product_name} matches {len(new_matches)} safety alert(s). Review below.", "error"
        )
    another = (await request.form()).get("add_another")
    return _redirect(
        "/app/inventory/new" if another else "/app/inventory",
        f"Added {holding.product_name}. No current alerts match it; Batchlight will tell you if one does.",
    )


def _own_holding(db, auth, holding_id: uuid.UUID) -> Holding:
    holding = db.scalar(
        select(Holding).where(Holding.id == holding_id, Holding.org_id == auth.org.id, Holding.archived_at.is_(None))
    )
    if holding is None:
        raise HTTPException(404)
    return holding


@router.get("/inventory/{holding_id}/edit")
def edit_holding(request: Request, holding_id: uuid.UUID, db: DB, auth: OrgAuth):
    holding = _own_holding(db, auth, holding_id)
    values = {name: getattr(holding, name) for name in FORM_FIELDS}
    values = {k: ("" if v is None else str(v)) for k, v in values.items()}
    return render(request, "app/holding_form.html", {"holding": holding, "form": values, "errors": {}, "welcome": False})


@router.post("/inventory/{holding_id}/edit", dependencies=[CSRF])
async def save_holding(request: Request, holding_id: uuid.UUID, db: DB, auth: OrgAuth):
    holding = _own_holding(db, auth, holding_id)
    values = await _read_holding_form(request)
    try:
        data = HoldingInput(**values)
    except ValidationError as exc:
        return render(
            request,
            "app/holding_form.html",
            {"holding": holding, "form": values, "errors": _form_errors(exc), "welcome": False},
            status_code=422,
        )
    new_matches = update_holding(db, holding, data)
    audit.record(db, "holding_updated", org_id=auth.org.id, user_id=auth.user.id, entity_type="holding", entity_id=holding.id)
    db.commit()
    if new_matches:
        return _redirect("/app/matches", f"{holding.product_name} now matches {len(new_matches)} safety alert(s).", "error")
    return _redirect("/app/inventory", f"Saved {holding.product_name}.")


@router.post("/inventory/{holding_id}/archive", dependencies=[CSRF])
def remove_holding(holding_id: uuid.UUID, db: DB, auth: OrgAuth):
    holding = _own_holding(db, auth, holding_id)
    archive_holding(db, holding)
    audit.record(db, "holding_archived", org_id=auth.org.id, user_id=auth.user.id, entity_type="holding", entity_id=holding.id)
    db.commit()
    return _redirect("/app/inventory", f"Removed {holding.product_name} from the list.", "info")


@router.get("/inventory/import")
def import_form(request: Request, auth: OrgAuth):
    return render(request, "app/import.html", {"errors": [], "report": None})


@router.post("/inventory/import", dependencies=[CSRF])
async def import_file(request: Request, db: DB, auth: OrgAuth, file: Annotated[UploadFile, File()]):
    raw = await file.read(2_000_001)
    rows, errors = parse_csv(raw)
    if errors:
        return render(
            request,
            "app/import.html",
            {"errors": errors[:50], "more_errors": max(0, len(errors) - 50), "report": None},
            status_code=422,
        )
    report = import_rows(db, auth.org.id, rows, auth.user.id)
    audit.record(
        db,
        "holdings_imported",
        org_id=auth.org.id,
        user_id=auth.user.id,
        created=report.created,
        duplicates=report.duplicates,
        matches=report.matches,
        filename=(file.filename or "")[:200],
    )
    db.commit()
    return render(request, "app/import.html", {"errors": [], "report": report})


@router.get("/inventory/template.csv")
def csv_template(auth: OrgAuth):
    return PlainTextResponse(
        CSV_TEMPLATE,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="batchlight-inventory-template.csv"'},
    )


@router.get("/inventory/export.csv")
def csv_export(db: DB, auth: OrgAuth):
    rows = db.scalars(
        select(Holding).where(Holding.org_id == auth.org.id, Holding.archived_at.is_(None)).order_by(Holding.product_name)
    ).all()
    return Response(
        export_csv(list(rows)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="batchlight-inventory.csv"'},
    )


# --------------------------------------------------------------------------- notifications


@router.get("/notifications")
def notifications(request: Request, db: DB, auth: OrgAuth):
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == auth.user.id, Notification.org_id == auth.org.id, Notification.channel == "in_app")
        .order_by(Notification.created_at.desc())
        .limit(100)
    ).all()
    return render(request, "app/notifications.html", {"notifications": rows})


@router.post("/notifications/read-all", dependencies=[CSRF])
def read_all(db: DB, auth: OrgAuth):
    db.execute(
        update(Notification)
        .where(Notification.user_id == auth.user.id, Notification.org_id == auth.org.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return _redirect("/app/notifications", "All notifications marked as read.", "info")


# --------------------------------------------------------------------------- team


@router.get("/team")
def team(request: Request, db: DB, auth: OrgAuth, invite_link: str = ""):
    members = db.scalars(
        select(Membership)
        .where(Membership.org_id == auth.org.id)
        .options(selectinload(Membership.user))
        .order_by(Membership.created_at)
    ).all()
    invitations = db.scalars(
        select(Invitation)
        .where(Invitation.org_id == auth.org.id, Invitation.accepted_at.is_(None), Invitation.expires_at > datetime.now(UTC))
        .order_by(Invitation.created_at.desc())
    ).all()
    return render(
        request,
        "app/team.html",
        {"members": members, "invitations": invitations, "roles": ROLES, "errors": {}, "invite_link": None},
    )


@router.post("/team/invite", dependencies=[CSRF])
def invite(request: Request, db: DB, auth: ManagerAuth, email: F = "", role: F = "member"):
    normalised = normalise_email(email)
    errors = {}
    if not normalised:
        errors["email"] = "Enter a valid email address."
    if role not in ROLES or (role == "owner" and not auth.can("owner")):
        errors["role"] = "Choose a role you are allowed to grant."
    if normalised and db.scalar(select(Membership).join(User).where(Membership.org_id == auth.org.id, User.email == normalised)):
        errors["email"] = "This person is already a member."
    members = db.scalars(select(Membership).where(Membership.org_id == auth.org.id).options(selectinload(Membership.user))).all()
    invitations = db.scalars(
        select(Invitation).where(
            Invitation.org_id == auth.org.id, Invitation.accepted_at.is_(None), Invitation.expires_at > datetime.now(UTC)
        )
    ).all()
    if errors:
        return render(
            request,
            "app/team.html",
            {
                "members": members,
                "invitations": invitations,
                "roles": ROLES,
                "errors": errors,
                "invite_link": None,
                "form": {"email": email, "role": role},
            },
            status_code=422,
        )
    token = new_token()
    invitation = Invitation(
        org_id=auth.org.id,
        email=normalised,
        role=role,
        token_hash=token_hash(token),
        invited_by=auth.user.id,
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    db.add(invitation)
    link = f"{get_settings().base_url}/invite/{token}"
    result = send_email(
        normalised,
        f"{auth.user.full_name} invited you to {auth.org.name} on Batchlight",
        f"{auth.user.full_name} invited you to join {auth.org.name} on Batchlight as a {role}.\n\n"
        f"Accept within 14 days: {link}\n\nBatchlight tells you when medicines you hold are affected by safety alerts.",
    )
    audit.record(db, "member_invited", org_id=auth.org.id, user_id=auth.user.id, subject=normalised, role=role)
    db.commit()
    invitations = [*invitations, invitation]
    message = "Invitation sent." if result.status == "sent" else "Email is not configured, so share this link with them yourself."
    return render(
        request,
        "app/team.html",
        {
            "members": members,
            "invitations": invitations,
            "roles": ROLES,
            "errors": {},
            "invite_link": link,
            "invite_message": message,
        },
    )


def _target_membership(db, auth, user_id: uuid.UUID) -> Membership:
    membership = db.get(Membership, (user_id, auth.org.id))
    if membership is None:
        raise HTTPException(404)
    return membership


def _owner_count(db, org_id) -> int:
    return (
        db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == "owner"))
        or 0
    )


@router.post("/team/members/{user_id}/role", dependencies=[CSRF])
def change_role(user_id: uuid.UUID, db: DB, auth: ManagerAuth, role: F = ""):
    membership = _target_membership(db, auth, user_id)
    if role not in ROLES:
        return _redirect("/app/team", "Choose a valid role.", "error")
    if (role == "owner" or membership.role == "owner") and not auth.can("owner"):
        return _redirect("/app/team", "Only owners can grant or remove the owner role.", "error")
    if membership.role == "owner" and role != "owner" and _owner_count(db, auth.org.id) <= 1:
        return _redirect("/app/team", "A workspace needs at least one owner.", "error")
    membership.role = role
    audit.record(
        db, "member_role_changed", org_id=auth.org.id, user_id=auth.user.id, entity_type="user", entity_id=user_id, role=role
    )
    db.commit()
    return _redirect("/app/team", "Role updated.")


@router.post("/team/members/{user_id}/remove", dependencies=[CSRF])
def remove_member(user_id: uuid.UUID, db: DB, auth: ManagerAuth):
    membership = _target_membership(db, auth, user_id)
    if membership.role == "owner" and not auth.can("owner"):
        return _redirect("/app/team", "Only owners can remove an owner.", "error")
    if membership.role == "owner" and _owner_count(db, auth.org.id) <= 1:
        return _redirect("/app/team", "A workspace needs at least one owner.", "error")
    db.delete(membership)
    db.execute(
        update(Session_).where(Session_.user_id == user_id, Session_.active_org_id == auth.org.id).values(active_org_id=None)
    )
    audit.record(db, "member_removed", org_id=auth.org.id, user_id=auth.user.id, entity_type="user", entity_id=user_id)
    db.commit()
    if user_id == auth.user.id:
        return _redirect("/", "You left the workspace.", "info")
    return _redirect("/app/team", "Member removed.", "info")


@router.post("/team/invitations/{invitation_id}/revoke", dependencies=[CSRF])
def revoke_invitation(invitation_id: uuid.UUID, db: DB, auth: ManagerAuth):
    invitation = db.scalar(select(Invitation).where(Invitation.id == invitation_id, Invitation.org_id == auth.org.id))
    if invitation is None:
        raise HTTPException(404)
    db.delete(invitation)
    db.commit()
    return _redirect("/app/team", "Invitation revoked.", "info")


# --------------------------------------------------------------------------- settings


@router.get("/settings")
def settings_page(request: Request, auth: OrgAuth):
    return render(request, "app/settings.html", {"countries": COUNTRIES, "kinds": ORG_KINDS, "errors": {}})


@router.post("/settings/workspace", dependencies=[CSRF])
def save_workspace(
    request: Request, db: DB, auth: ManagerAuth, name: F = "", kind: F = "", country_code: F = "", notify_email: F = ""
):
    errors = {}
    if not 2 <= len(name.strip()) <= 200:
        errors["name"] = "Enter a workspace name."
    if kind not in ORG_KINDS:
        errors["kind"] = "Choose a workspace type."
    if not valid_country(country_code):
        errors["country_code"] = "Choose a country."
    if errors:
        return render(
            request, "app/settings.html", {"countries": COUNTRIES, "kinds": ORG_KINDS, "errors": errors}, status_code=422
        )
    org = db.get(Organization, auth.org.id)
    org.name, org.kind, org.country_code, org.notify_email = name.strip(), kind, country_code.upper(), notify_email == "on"
    audit.record(db, "workspace_updated", org_id=org.id, user_id=auth.user.id)
    db.commit()
    return _redirect("/app/settings", "Workspace settings saved.")


@router.post("/settings/profile", dependencies=[CSRF])
def save_profile(request: Request, db: DB, auth: UserAuth, full_name: F = ""):
    if not 2 <= len(full_name.strip()) <= 200:
        return _redirect("/app/settings", "Enter your name.", "error")
    user = db.get(User, auth.user.id)
    user.full_name = full_name.strip()
    db.commit()
    return _redirect("/app/settings", "Profile saved.")


@router.post("/settings/password", dependencies=[CSRF])
def change_password(request: Request, db: DB, auth: UserAuth, current_password: F = "", new_password: F = ""):
    user = db.get(User, auth.user.id)
    if not verify_password(user.password_hash, current_password):
        return _redirect("/app/settings", "Your current password is not correct.", "error")
    problem = password_problem(new_password, user.email)
    if problem:
        return _redirect("/app/settings", problem, "error")
    user.password_hash = hash_password(new_password)
    db.execute(Session_.__table__.delete().where(Session_.user_id == user.id, Session_.id != auth.session_id))
    audit.record(db, "password_changed", user_id=user.id, ip=client_ip(request))
    db.commit()
    return _redirect("/app/settings", "Password changed. Your other sessions were signed out.")


# --------------------------------------------------------------------------- API keys


@router.get("/api-keys")
def api_keys(request: Request, db: DB, auth: ManagerAuth):
    keys = db.scalars(select(ApiKey).where(ApiKey.org_id == auth.org.id).order_by(ApiKey.created_at.desc())).all()
    return render(request, "app/api_keys.html", {"keys": keys, "new_key": None})


@router.post("/api-keys", dependencies=[CSRF])
def create_api_key(request: Request, db: DB, auth: ManagerAuth, name: F = ""):
    name = name.strip()[:100] or "API key"
    full, prefix, digest = new_api_key()
    db.add(ApiKey(org_id=auth.org.id, name=name, prefix=prefix, key_hash=digest, created_by=auth.user.id))
    audit.record(db, "api_key_created", org_id=auth.org.id, user_id=auth.user.id, prefix=prefix)
    db.commit()
    keys = db.scalars(select(ApiKey).where(ApiKey.org_id == auth.org.id).order_by(ApiKey.created_at.desc())).all()
    return render(request, "app/api_keys.html", {"keys": keys, "new_key": full})


@router.post("/api-keys/{key_id}/revoke", dependencies=[CSRF])
def revoke_api_key(key_id: uuid.UUID, db: DB, auth: ManagerAuth):
    key = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == auth.org.id))
    if key is None:
        raise HTTPException(404)
    key.revoked_at = key.revoked_at or datetime.now(UTC)
    audit.record(db, "api_key_revoked", org_id=auth.org.id, user_id=auth.user.id, prefix=key.prefix)
    db.commit()
    return _redirect("/app/api-keys", "API key revoked.", "info")


# --------------------------------------------------------------------------- workspaces


@router.get("/workspaces/new")
def new_workspace(request: Request, auth: UserAuth):
    return render(request, "app/workspace_new.html", {"countries": COUNTRIES, "kinds": ORG_KINDS, "errors": {}, "form": {}})


@router.post("/workspaces", dependencies=[CSRF])
def create_workspace(request: Request, db: DB, auth: UserAuth, name: F = "", kind: F = "pharmacy", country_code: F = ""):
    errors = {}
    if not 2 <= len(name.strip()) <= 200:
        errors["name"] = "Enter a workspace name."
    if kind not in ORG_KINDS:
        errors["kind"] = "Choose a workspace type."
    if not valid_country(country_code):
        errors["country_code"] = "Choose a country."
    if errors:
        return render(
            request,
            "app/workspace_new.html",
            {
                "countries": COUNTRIES,
                "kinds": ORG_KINDS,
                "errors": errors,
                "form": {"name": name, "kind": kind, "country_code": country_code},
            },
            status_code=422,
        )
    org = Organization(name=name.strip(), kind=kind, country_code=country_code.upper())
    db.add(org)
    db.flush()
    db.add(Membership(user_id=auth.user.id, org_id=org.id, role="owner"))
    db.get(Session_, auth.session_id).active_org_id = org.id
    audit.record(db, "workspace_created", org_id=org.id, user_id=auth.user.id)
    db.commit()
    return _redirect("/app/inventory/new", f"Created {org.name}.")


@router.post("/workspaces/switch", dependencies=[CSRF])
def switch_workspace(db: DB, auth: UserAuth, org_id: F = ""):
    try:
        target = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(404) from None
    if not any(m.org_id == target for m in auth.memberships):
        raise HTTPException(404)
    db.get(Session_, auth.session_id).active_org_id = target
    db.commit()
    return _redirect("/app")
