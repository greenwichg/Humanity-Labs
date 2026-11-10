"""Sign up, sign in, sign out, password reset and invitations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, or_, select

from ...config import get_settings
from ...db import session_scope
from ...models import ORG_KINDS, AuditEvent, Invitation, Membership, Organization, PasswordReset, Session_, User
from ...security import hash_password, new_token, password_problem, token_hash, verify_password
from ...services import audit
from ...services.email import SendResult, log_delivery_failure, redact, send_email
from ..auth import CSRF, DB, OptionalAuth, client_ip, create_session, destroy_session, flash, safe_next
from ..countries import COUNTRIES, valid_country
from ..templating import render

router = APIRouter()
log = logging.getLogger("batchlight.auth")
Text = Annotated[str, Form()]
OptText = Annotated[str, Form()]


def normalise_email(value: str) -> str | None:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def throttled(db, email: str, ip: str | None) -> bool:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(minutes=settings.login_window_minutes)
    failures = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.action == "login_failed",
            AuditEvent.created_at >= since,
            or_(AuditEvent.subject == email, AuditEvent.ip == ip) if ip else AuditEvent.subject == email,
        )
    )
    return (failures or 0) >= settings.login_max_failures


# --------------------------------------------------------------------------- sign up


@router.get("/signup", include_in_schema=False)
def signup_form(request: Request, auth: OptionalAuth):
    if auth:
        return RedirectResponse("/app", status_code=303)
    return render(
        request, "auth/signup.html", {"form": {"org_kind": "pharmacy"}, "errors": {}, "countries": COUNTRIES, "kinds": ORG_KINDS}
    )


@router.post("/signup", include_in_schema=False, dependencies=[CSRF])
def signup(
    request: Request,
    db: DB,
    full_name: Text = "",
    email: Text = "",
    password: Text = "",
    org_name: Text = "",
    org_kind: Text = "pharmacy",
    country_code: Text = "",
):
    form = {
        "full_name": full_name.strip(),
        "email": email.strip(),
        "org_name": org_name.strip(),
        "org_kind": org_kind,
        "country_code": country_code.upper(),
    }
    errors: dict[str, str] = {}
    if not 2 <= len(form["full_name"]) <= 200:
        errors["full_name"] = "Enter your name."
    normalised = normalise_email(email)
    if not normalised:
        errors["email"] = "Enter a valid email address."
    problem = password_problem(password, normalised)
    if problem:
        errors["password"] = problem
    if not 2 <= len(form["org_name"]) <= 200:
        errors["org_name"] = "Enter a name for your workspace, such as your pharmacy's name or 'Dad's medicines'."
    if org_kind not in ORG_KINDS:
        errors["org_kind"] = "Choose what kind of workspace this is."
    if not valid_country(form["country_code"]):
        errors["country_code"] = "Choose a country."
    if not errors and db.scalar(select(User.id).where(User.email == normalised)):
        errors["email"] = (
            "This email can't be used to create an account. If you already have one, sign in or reset your password."
        )
    if errors:
        return render(
            request,
            "auth/signup.html",
            {"form": form, "errors": errors, "countries": COUNTRIES, "kinds": ORG_KINDS},
            status_code=422,
        )
    user = User(email=normalised, full_name=form["full_name"], password_hash=hash_password(password))
    org = Organization(name=form["org_name"], kind=org_kind, country_code=form["country_code"])
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    audit.record(db, "user_signed_up", org_id=org.id, user_id=user.id, subject=normalised, ip=client_ip(request))
    response = RedirectResponse("/app/inventory/new?welcome=1", status_code=303)
    create_session(db, response, request, user, org.id)
    db.commit()
    flash(response, f"Welcome to Batchlight. Add the first item in {org.name} to start matching it against alerts.")
    return response


# --------------------------------------------------------------------------- sign in / out


@router.get("/login", include_in_schema=False)
def login_form(request: Request, auth: OptionalAuth, next: str = ""):
    if auth:
        return RedirectResponse(safe_next(next), status_code=303)
    return render(request, "auth/login.html", {"next": safe_next(next, ""), "email": "", "error": None})


@router.post("/login", include_in_schema=False, dependencies=[CSRF])
def login(request: Request, db: DB, email: Text = "", password: Text = "", next: OptText = ""):
    ip = client_ip(request)
    normalised = normalise_email(email) or email.strip().lower()[:320]
    context = {"next": safe_next(next, ""), "email": email.strip()}
    if throttled(db, normalised, ip):
        return render(
            request,
            "auth/login.html",
            {**context, "error": "Too many failed attempts. Wait 15 minutes and try again, or reset your password."},
            status_code=429,
        )
    user = db.scalar(select(User).where(User.email == normalised))
    if user is None or not verify_password(user.password_hash, password):
        audit.record(db, "login_failed", subject=normalised, ip=ip)
        db.commit()
        return render(
            request, "auth/login.html", {**context, "error": "That email and password don't match an account."}, status_code=401
        )
    response = RedirectResponse(safe_next(next), status_code=303)
    create_session(db, response, request, user)
    audit.record(db, "login_succeeded", user_id=user.id, subject=normalised, ip=ip)
    db.commit()
    return response


@router.post("/logout", include_in_schema=False, dependencies=[CSRF])
def logout(request: Request, db: DB, auth: OptionalAuth):
    response = RedirectResponse("/", status_code=303)
    destroy_session(db, response, auth)
    db.commit()
    flash(response, "You have signed out.", "info")
    return response


# --------------------------------------------------------------------------- password reset


@router.get("/forgot-password", include_in_schema=False)
def forgot_form(request: Request, auth: OptionalAuth):
    return render(request, "auth/forgot.html", {"sent": False})


@router.post("/forgot-password", include_in_schema=False, dependencies=[CSRF])
def forgot(request: Request, db: DB, background: BackgroundTasks, email: Text = ""):
    """Every outcome (no such account, too many requests, sent, delivery failed) gets the same page and status, and
    the email goes out after the response, so neither the answer nor its timing shows whether an address has an
    account. What happened is kept in the audit log and the application log instead."""
    normalised = normalise_email(email)
    user = db.scalar(select(User).where(User.email == normalised)) if normalised else None
    if user:
        recent = (
            db.scalar(
                select(func.count())
                .select_from(PasswordReset)
                .where(PasswordReset.user_id == user.id, PasswordReset.created_at >= datetime.now(UTC) - timedelta(hours=1))
            )
            or 0
        )
        if recent < 3:
            token = new_token()
            # Created unusable (already expired). Only a confirmed send makes it work, so a failed send, an error in
            # the background task or a crashed process can never leave a working link behind.
            reset_row = PasswordReset(
                user_id=user.id, token_hash=token_hash(token), expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
            db.add(reset_row)
            db.flush()
            background.add_task(deliver_password_reset, reset_row.id, user.id, user.email, user.full_name, token)
            audit.record(db, "password_reset_requested", user_id=user.id, ip=client_ip(request))
        else:
            audit.record(db, "password_reset_rate_limited", user_id=user.id, ip=client_ip(request))
        db.commit()
    return render(request, "auth/forgot.html", {"sent": True})


def deliver_password_reset(reset_id, user_id, address: str, full_name: str, token: str) -> None:
    """Runs after the response. The link starts working only once the email has gone out; otherwise it stays
    expired (still counting towards the hourly limit). Outcomes are audited and logged by user ID, never by address:
    password_reset_email_sent or password_reset_email_failed. A request with neither was cut off (for example by a
    restart) and its link never worked."""
    link = f"{get_settings().base_url}/reset-password/{token}"
    try:
        result = send_email(
            address,
            "Reset your Batchlight password",
            f"Hello {full_name},\n\nUse this link within one hour to choose a new password:\n{link}\n\n"
            "If you didn't ask for this, you can ignore this email.",
        )
    except Exception as exc:  # noqa: BLE001 - any error must end as a recorded failure, not an unaccounted request
        result = SendResult("failed", redact(f"{type(exc).__name__}: {exc}")[:300])
        log_delivery_failure(address, result.error)
    try:
        with session_scope() as session:
            if result.status == "sent":
                reset_row = session.get(PasswordReset, reset_id)
                if reset_row is not None and reset_row.used_at is None:
                    reset_row.expires_at = datetime.now(UTC) + timedelta(hours=1)
                audit.record(session, "password_reset_email_sent", user_id=user_id)
            else:
                audit.record(
                    session,
                    "password_reset_email_failed",
                    user_id=user_id,
                    status=result.status,
                    error=redact(result.error or "")[:200],
                )
    except Exception:  # noqa: BLE001 - the link stays unusable; say so in the log
        log.exception("password reset outcome not recorded: user_id=%s status=%s", user_id, result.status)
        return
    if result.status != "sent":
        log.warning("password reset email not delivered: user_id=%s status=%s", user_id, result.status)


def _valid_reset(db, token: str) -> PasswordReset | None:
    reset = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == token_hash(token)))
    if reset is None or reset.used_at is not None or reset.expires_at < datetime.now(UTC):
        return None
    return reset


@router.get("/reset-password/{token}", include_in_schema=False)
def reset_form(request: Request, token: str, db: DB, auth: OptionalAuth):
    return render(request, "auth/reset.html", {"valid": _valid_reset(db, token) is not None, "token": token, "error": None})


@router.post("/reset-password/{token}", include_in_schema=False, dependencies=[CSRF])
def reset(request: Request, token: str, db: DB, password: Text = ""):
    reset_row = _valid_reset(db, token)
    if reset_row is None:
        return render(request, "auth/reset.html", {"valid": False, "token": token, "error": None}, status_code=400)
    user = db.get(User, reset_row.user_id)
    problem = password_problem(password, user.email)
    if problem:
        return render(request, "auth/reset.html", {"valid": True, "token": token, "error": problem}, status_code=422)
    user.password_hash = hash_password(password)
    reset_row.used_at = datetime.now(UTC)
    db.execute(delete(Session_).where(Session_.user_id == user.id))
    audit.record(db, "password_reset_completed", user_id=user.id, ip=client_ip(request))
    response = RedirectResponse("/app", status_code=303)
    create_session(db, response, request, user)
    db.commit()
    flash(response, "Your password has been changed and other sessions were signed out.")
    return response


# --------------------------------------------------------------------------- invitations


def _valid_invitation(db, token: str) -> Invitation | None:
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
    if invitation is None or invitation.accepted_at is not None or invitation.expires_at < datetime.now(UTC):
        return None
    return invitation


@router.get("/invite/{token}", include_in_schema=False)
def invite_page(request: Request, token: str, db: DB, auth: OptionalAuth):
    invitation = _valid_invitation(db, token)
    existing = db.scalar(select(User.id).where(User.email == invitation.email)) if invitation else None
    return render(
        request,
        "auth/invite.html",
        {"invitation": invitation, "token": token, "has_account": existing is not None, "errors": {}, "form": {}},
    )


@router.post("/invite/{token}", include_in_schema=False, dependencies=[CSRF])
def accept_invite(request: Request, token: str, db: DB, auth: OptionalAuth, full_name: OptText = "", password: OptText = ""):
    invitation = _valid_invitation(db, token)
    if invitation is None:
        return render(
            request,
            "auth/invite.html",
            {"invitation": None, "token": token, "has_account": False, "errors": {}, "form": {}},
            status_code=400,
        )
    user = db.scalar(select(User).where(User.email == invitation.email))
    if user is not None:
        if auth is None or auth.user.id != user.id:
            return RedirectResponse(f"/login?next=/invite/{token}", status_code=303)
    else:
        errors = {}
        if not 2 <= len(full_name.strip()) <= 200:
            errors["full_name"] = "Enter your name."
        problem = password_problem(password, invitation.email)
        if problem:
            errors["password"] = problem
        if errors:
            return render(
                request,
                "auth/invite.html",
                {
                    "invitation": invitation,
                    "token": token,
                    "has_account": False,
                    "errors": errors,
                    "form": {"full_name": full_name},
                },
                status_code=422,
            )
        user = User(email=invitation.email, full_name=full_name.strip(), password_hash=hash_password(password))
        db.add(user)
        db.flush()
    if not db.get(Membership, (user.id, invitation.org_id)):
        db.add(Membership(user_id=user.id, org_id=invitation.org_id, role=invitation.role))
    invitation.accepted_at = datetime.now(UTC)
    audit.record(
        db, "invitation_accepted", org_id=invitation.org_id, user_id=user.id, entity_type="invitation", entity_id=invitation.id
    )
    response = RedirectResponse("/app", status_code=303)
    if auth is None or auth.user.id != user.id:
        create_session(db, response, request, user, invitation.org_id)
    else:
        session_row = db.get(Session_, auth.session_id)
        session_row.active_org_id = invitation.org_id
    db.commit()
    flash(response, f"You have joined {invitation.organization.name}.")
    return response
