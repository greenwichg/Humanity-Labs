"""Sessions, CSRF protection, flash messages and authorisation dependencies."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..db import get_db
from ..models import Membership, Notification, Organization, Session_, User
from ..security import constant_time_equals, new_token, token_hash

SESSION_COOKIE = "bl_session"
CSRF_COOKIE = "bl_csrf"
FLASH_COOKIE = "bl_flash"
ROLE_RANK = {"member": 1, "manager": 2, "owner": 3}

DB = Annotated[Session, Depends(get_db)]


class LoginRequired(Exception):
    def __init__(self, next_url: str) -> None:
        self.next_url = next_url


class WorkspaceRequired(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message: str = "You do not have permission to do that.") -> None:
        self.message = message


class CsrfFailed(Exception):
    pass


@dataclass
class AuthContext:
    session_id: str
    user: User
    memberships: list[Membership] = field(default_factory=list)
    org: Organization | None = None
    role: str | None = None
    unread: int = 0

    def can(self, role: str) -> bool:
        return ROLE_RANK.get(self.role or "", 0) >= ROLE_RANK[role]


def client_ip(request: Request) -> str | None:
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The rightmost entry is the one our proxy appended; anything to its left came from the client.
            return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else None


# --------------------------------------------------------------------------- sessions


def create_session(db: Session, response: Response, request: Request, user: User, org_id: uuid.UUID | None = None) -> None:
    settings = get_settings()
    token = new_token()
    if org_id is None:
        first = db.scalar(select(Membership.org_id).where(Membership.user_id == user.id).order_by(Membership.created_at))
        org_id = first
    db.add(
        Session_(
            id=token_hash(token),
            user_id=user.id,
            active_org_id=org_id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_days),
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    user.last_login_at = datetime.now(UTC)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def destroy_session(db: Session, response: Response, auth: AuthContext | None) -> None:
    if auth:
        row = db.get(Session_, auth.session_id)
        if row:
            db.delete(row)
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_auth(request: Request, db: DB) -> AuthContext | None:
    if getattr(request.state, "auth_loaded", False):
        return request.state.auth
    token = request.cookies.get(SESSION_COOKIE)
    auth = None
    if token:
        now = datetime.now(UTC)
        row = db.scalar(select(Session_).where(Session_.id == token_hash(token)).options(joinedload(Session_.user)))
        if row and row.expires_at > now:
            memberships = db.scalars(
                select(Membership)
                .where(Membership.user_id == row.user_id)
                .options(joinedload(Membership.organization))
                .order_by(Membership.created_at)
            ).all()
            active = next((m for m in memberships if m.org_id == row.active_org_id), memberships[0] if memberships else None)
            auth = AuthContext(
                row.id, row.user, list(memberships), active.organization if active else None, active.role if active else None
            )
            if auth.org is not None:
                auth.unread = (
                    db.scalar(
                        select(func.count())
                        .select_from(Notification)
                        .where(
                            Notification.user_id == auth.user.id,
                            Notification.org_id == auth.org.id,
                            Notification.channel == "in_app",
                            Notification.read_at.is_(None),
                        )
                    )
                    or 0
                )
            if now - row.last_seen_at > timedelta(hours=12):
                row.last_seen_at = now
                row.expires_at = now + timedelta(days=get_settings().session_days)
                db.commit()
    request.state.auth = auth
    request.state.auth_loaded = True
    return auth


def _next_url(request: Request) -> str:
    return request.url.path + (f"?{request.url.query}" if request.url.query else "")


def require_user(request: Request, auth: Annotated[AuthContext | None, Depends(get_auth)]) -> AuthContext:
    if auth is None:
        raise LoginRequired(_next_url(request))
    return auth


def require_org(auth: Annotated[AuthContext, Depends(require_user)]) -> AuthContext:
    if auth.org is None:
        raise WorkspaceRequired()
    return auth


def require_manager(auth: Annotated[AuthContext, Depends(require_org)]) -> AuthContext:
    if not auth.can("manager"):
        raise Forbidden("Only workspace owners and managers can do that.")
    return auth


def require_owner(auth: Annotated[AuthContext, Depends(require_org)]) -> AuthContext:
    if not auth.can("owner"):
        raise Forbidden("Only workspace owners can do that.")
    return auth


def require_curator(auth: Annotated[AuthContext, Depends(require_user)]) -> AuthContext:
    if not auth.user.is_curator:
        raise Forbidden("This area is for Batchlight data curators.")
    return auth


OptionalAuth = Annotated[AuthContext | None, Depends(get_auth)]
UserAuth = Annotated[AuthContext, Depends(require_user)]
OrgAuth = Annotated[AuthContext, Depends(require_org)]
ManagerAuth = Annotated[AuthContext, Depends(require_manager)]
OwnerAuth = Annotated[AuthContext, Depends(require_owner)]
CuratorAuth = Annotated[AuthContext, Depends(require_curator)]


def safe_next(value: str | None, default: str = "/app") -> str:
    """Only same-site paths. Browsers drop tabs and newlines, so "/\t/evil" would become "//evil"."""
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and all(ch.isprintable() and not ch.isspace() for ch in value)
    ):
        return value
    return default


# --------------------------------------------------------------------------- CSRF


def csrf_token(request: Request) -> str:
    """Per-browser token, mirrored in a cookie by the middleware (double-submit)."""
    token = getattr(request.state, "csrf_token", None)
    if token is None:
        token = request.cookies.get(CSRF_COOKIE) or new_token(24)
        request.state.csrf_token = token
    return token


async def csrf_protect(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    submitted = request.headers.get("x-csrf-token")
    if submitted is None:
        form = await request.form()
        submitted = form.get("csrf_token")
    if not cookie or not isinstance(submitted, str) or not constant_time_equals(cookie, submitted):
        raise CsrfFailed()


CSRF = Depends(csrf_protect)


# --------------------------------------------------------------------------- flash


def flash(response: Response, message: str, level: str = "success") -> None:
    response.set_cookie(
        FLASH_COOKIE,
        quote(json.dumps([level, message[:300]])),
        max_age=60,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite="lax",
        path="/",
    )


def pop_flash(request: Request) -> tuple[str, str] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    request.state.clear_flash = True
    try:
        level, message = json.loads(unquote(raw))
        return (str(level), str(message)) if level in ("success", "error", "info") else None
    except (ValueError, TypeError):
        return None
