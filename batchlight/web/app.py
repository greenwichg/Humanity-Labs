"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import get_settings
from ..db import session_scope
from ..ingest import ensure_sources
from ..services.email import production_email_problems
from .auth import CSRF_COOKIE, FLASH_COOKIE, CsrfFailed, Forbidden, LoginRequired, WorkspaceRequired, csrf_token
from .routes import admin, admin_food, api, auth_routes, community, food, infra, public, workspace
from .templating import render

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("batchlight.web")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with session_scope() as session:
            ensure_sources(session)
    except Exception:  # noqa: BLE001 - the app can still serve health checks while the DB is down
        log.exception("could not register sources at startup")
    yield


def is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.is_production and len(settings.secret_key) < 32:
        raise RuntimeError("Set BATCHLIGHT_SECRET_KEY to a random value of at least 32 characters in production")
    if settings.is_production and (problems := production_email_problems(settings)):
        # Password resets and invitations need working email; never run production pretending otherwise.
        raise RuntimeError("Email is not set up for production: " + "; ".join(problems))
    app = FastAPI(
        title="Batchlight API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        description="Batch-level medicine safety alerts from regulators worldwide. "
        "Public endpoints need no key; workspace endpoints take `Authorization: Bearer <API key>`.",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", DOCS_CSP if request.url.path == "/api/docs" else CSP)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        if settings.secure_cookies:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        token = getattr(request.state, "csrf_token", None)
        if token and request.cookies.get(CSRF_COOKIE) != token:
            response.set_cookie(
                CSRF_COOKIE,
                token,
                max_age=60 * 60 * 24 * 30,
                httponly=True,
                secure=settings.secure_cookies,
                samesite="lax",
                path="/",
            )
        if getattr(request.state, "clear_flash", False) and not any(
            cookie.startswith(f"{FLASH_COOKIE}=") for cookie in response.headers.getlist("set-cookie")
        ):
            response.delete_cookie(FLASH_COOKIE, path="/")
        if request.url.path.startswith(("/app", "/admin", "/login", "/signup", "/invite", "/reset-password")):
            headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        if is_api(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        from urllib.parse import quote

        return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)

    @app.exception_handler(WorkspaceRequired)
    async def workspace_required(request: Request, exc: WorkspaceRequired):
        return RedirectResponse("/app/workspaces/new", status_code=303)

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        if is_api(request):
            return JSONResponse({"detail": exc.message}, status_code=403)
        return render(request, "error.html", {"title": "Not allowed", "message": exc.message}, status_code=403)

    @app.exception_handler(CsrfFailed)
    async def csrf_failed(request: Request, exc: CsrfFailed):
        csrf_token(request)  # make sure the browser gets a fresh cookie
        return render(
            request,
            "error.html",
            {
                "title": "Form expired",
                "message": "Your form expired or came from another site. Go back, refresh the page and try again.",
            },
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if is_api(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
        titles = {404: "Page not found", 405: "Not allowed", 401: "Sign in required", 403: "Not allowed"}
        message = (
            exc.detail
            if exc.status_code != 404
            else "We couldn't find that page. It may have moved, or you may not have access to it."
        )
        return render(
            request,
            "error.html",
            {"title": titles.get(exc.status_code, "Something went wrong"), "message": message},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        log.info("validation error on %s %s: %s", request.method, request.url.path, jsonable_errors(exc))
        if is_api(request):
            return JSONResponse({"detail": jsonable_errors(exc)}, status_code=422)
        return render(
            request,
            "error.html",
            {"title": "Check your input", "message": "Some of the information sent was not valid. Go back and try again."},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        if is_api(request):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        return render(
            request,
            "error.html",
            {"title": "Something went wrong", "message": "An unexpected error occurred. It has been logged. Please try again."},
            status_code=500,
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    for router in (
        public.router,
        food.router,
        infra.router,
        community.router,
        auth_routes.router,
        workspace.router,
        admin.router,
        admin_food.router,
        api.router,
    ):
        app.include_router(router)
    return app


def jsonable_errors(exc: RequestValidationError) -> list[dict]:
    return [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
