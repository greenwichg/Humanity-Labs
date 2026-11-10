"""Email in production: working SMTP is required, and the site never says an email went out when it did not."""

from __future__ import annotations

import html
import re
import smtplib
import sys

import pytest
from sqlalchemy import func, select

from batchlight.config import get_settings
from batchlight.models import AuditEvent, PasswordReset
from batchlight.services import email
from batchlight.services.email import OUTBOX, SendResult
from tests.conftest import post_form, signup

PRODUCTION_SMTP = {
    "BATCHLIGHT_ENV": "production",
    "BATCHLIGHT_SECRET_KEY": "x" * 40,
    "BATCHLIGHT_EMAIL_BACKEND": "smtp",
    "BATCHLIGHT_SMTP_HOST": "email-smtp.ap-south-1.amazonaws.com",
    "BATCHLIGHT_SMTP_USERNAME": "smtp-user",
    "BATCHLIGHT_SMTP_PASSWORD": "smtp-secret",
    "BATCHLIGHT_EMAIL_FROM": "Humanity Labs <alerts@humanitylabs.in>",
}


@pytest.fixture
def settings_env(monkeypatch):
    """Set environment variables for one test and read settings afresh, restoring the test settings afterwards."""

    def apply(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return get_settings()

    yield apply
    get_settings.cache_clear()


# --------------------------------------------------------------------------- production requires working email


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"BATCHLIGHT_EMAIL_BACKEND": "console"}, "must be smtp"),
        ({"BATCHLIGHT_EMAIL_BACKEND": "disabled"}, "must be smtp"),
        ({"BATCHLIGHT_SMTP_PASSWORD": "not-set"}, "placeholder"),
        ({"BATCHLIGHT_SMTP_PASSWORD": ""}, "placeholder"),
        ({"BATCHLIGHT_SMTP_USERNAME": ""}, "USERNAME is empty"),
        ({"BATCHLIGHT_SMTP_HOST": "localhost"}, "SMTP_HOST"),
        ({"BATCHLIGHT_SMTP_STARTTLS": "false"}, "STARTTLS"),
    ],
)
def test_production_refuses_to_start_without_working_email(settings_env, override, expected):
    from batchlight.web.app import create_app

    settings_env(**(PRODUCTION_SMTP | override))
    with pytest.raises(RuntimeError, match="Email is not set up for production") as error:
        create_app()
    assert expected in str(error.value)


def test_production_starts_with_complete_smtp_settings(settings_env):
    from batchlight.web.app import create_app

    settings = settings_env(**PRODUCTION_SMTP)
    assert email.production_email_problems(settings) == []
    assert create_app() is not None


def test_worker_refuses_to_run_in_production_without_email(settings_env, monkeypatch):
    from batchlight import worker

    settings_env(**(PRODUCTION_SMTP | {"BATCHLIGHT_EMAIL_BACKEND": "console"}))
    ran = []
    monkeypatch.setattr(worker, "run_once", lambda *a, **k: ran.append(1) or {})
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    with pytest.raises(SystemExit, match="Email is not set up for production"):
        worker.main()
    assert ran == []


# --------------------------------------------------------------------------- email-check (run before every deploy)


class FakeSMTP:
    """Stands in for smtplib.SMTP and records what was done; never delivers anything."""

    calls: list[str] = []
    refuse_login = False

    def __init__(self, host, port, timeout=None):
        FakeSMTP.calls.append(f"connect {host}:{port}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSMTP.calls.append("quit")

    def ehlo(self):
        FakeSMTP.calls.append("ehlo")

    def starttls(self):
        FakeSMTP.calls.append("starttls")

    def login(self, user, password):
        FakeSMTP.calls.append(f"login {user}")
        if FakeSMTP.refuse_login:
            raise smtplib.SMTPAuthenticationError(535, b"Authentication Credentials Invalid")

    def noop(self):
        FakeSMTP.calls.append("noop")
        return 250, b"OK"

    def send_message(self, *a, **k):  # pragma: no cover - must never be called by the check
        FakeSMTP.calls.append("SEND")


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.calls = []
    FakeSMTP.refuse_login = False
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


def test_email_check_logs_in_over_tls_without_sending(settings_env, fake_smtp, capsys):
    from batchlight.cli import main

    settings_env(**PRODUCTION_SMTP)
    assert main(["email-check"]) == 0
    assert fake_smtp.calls == [
        "connect email-smtp.ap-south-1.amazonaws.com:587",
        "ehlo",
        "starttls",
        "ehlo",
        "login smtp-user",
        "noop",
        "quit",
    ]
    assert "nothing was sent" in capsys.readouterr().out


def test_email_check_fails_on_refused_credentials(settings_env, fake_smtp, capsys):
    from batchlight.cli import main

    settings_env(**PRODUCTION_SMTP)
    fake_smtp.refuse_login = True
    assert main(["email-check"]) == 1
    assert "user name or password was refused (535)" in capsys.readouterr().out
    assert "SEND" not in fake_smtp.calls


def test_email_check_fails_on_incomplete_settings_without_connecting(settings_env, fake_smtp, capsys):
    from batchlight.cli import main

    settings_env(**(PRODUCTION_SMTP | {"BATCHLIGHT_SMTP_PASSWORD": "not-set"}))
    assert main(["email-check"]) == 1
    assert "placeholder" in capsys.readouterr().out
    assert fake_smtp.calls == []


# --------------------------------------------------------------------------- password reset: same answer in every case


OWNER = "owner@example.org"
UNIFORM_TEXT = "If that email belongs to an account, we'll try to send it a link to reset the password."


def forgot(client, address):
    response = post_form(client, "/forgot-password", {"email": address})
    return response.status_code, response.text, response.headers.get("content-type"), sorted(response.cookies.keys())


def valid_links(db) -> int:
    from datetime import UTC, datetime

    db.expire_all()
    return db.scalar(
        select(func.count())
        .select_from(PasswordReset)
        .where(PasswordReset.used_at.is_(None), PasswordReset.expires_at > datetime.now(UTC))
    )


def test_forgot_password_answer_is_identical_whatever_happened(client, make_client, db, monkeypatch, caplog):
    """No account, sent, delivery failed, delivery skipped, rate limited: same status, same body, same headers."""
    from batchlight.web.routes import auth_routes

    signup(client)
    visitor = make_client()
    real_send = auth_routes.send_email
    answers = {}

    answers["no account"] = forgot(visitor, "nobody@example.org")
    answers["sent"] = forgot(visitor, OWNER)
    monkeypatch.setattr(auth_routes, "send_email", lambda *a, **k: SendResult("failed", "SMTP 454 throttled"))
    with caplog.at_level("WARNING", logger="batchlight.auth"):
        answers["delivery failed"] = forgot(visitor, OWNER)
    monkeypatch.setattr(auth_routes, "send_email", lambda *a, **k: SendResult("skipped", "Email delivery is disabled"))
    answers["delivery skipped"] = forgot(visitor, OWNER)
    monkeypatch.setattr(auth_routes, "send_email", real_send)
    answers["rate limited"] = forgot(visitor, OWNER)  # the fourth request in the hour
    answers["unknown, mixed case"] = forgot(visitor, "Nobody@Example.org")

    reference = answers["no account"]
    assert reference[0] == 200
    assert UNIFORM_TEXT in html.unescape(reference[1])
    for case, answer in answers.items():
        assert answer[0] == reference[0], f"status differs for {case}"
        assert answer[1] == reference[1], f"body differs for {case}"
        assert answer[2:] == reference[2:], f"headers or cookies differ for {case}"
    text = html.unescape(reference[1])
    assert "we've sent" not in text and "couldn't send" not in text  # never claims either way

    # What actually happened is still recorded, by user, for operators.
    actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.id)).all()
    assert actions.count("password_reset_requested") == 3
    assert actions.count("password_reset_email_failed") == 2
    assert actions.count("password_reset_rate_limited") == 1
    failures = db.scalars(select(AuditEvent).where(AuditEvent.action == "password_reset_email_failed")).all()
    assert all(event.user_id is not None for event in failures)
    assert len(OUTBOX) == 1  # only the "sent" case delivered anything
    assert valid_links(db) == 1  # links whose email failed no longer work
    assert "password reset email not delivered" in caplog.text
    assert OWNER not in caplog.text  # logged by user ID, never by address


def test_failed_delivery_link_does_not_work(client, make_client, db, monkeypatch):
    from batchlight.web.routes import auth_routes

    signup(client)
    captured = {}

    def failing_send(to, subject, text):
        captured["path"] = re.search(r"http://testserver(/reset-password/\S+)", text).group(1)
        return SendResult("failed", "connection refused")

    monkeypatch.setattr(auth_routes, "send_email", failing_send)
    status, *_ = forgot(make_client(), OWNER)
    assert status == 200
    visitor = make_client()
    assert "This link has expired" in visitor.get(captured["path"]).text
    assert post_form(visitor, captured["path"], {"password": "a brand new passphrase"}).status_code == 400


def test_delivery_runs_after_the_response(client, make_client, monkeypatch):
    """The email is sent by a background task, so a slow mail server cannot reveal an account by timing."""
    from batchlight.db import session_scope
    from batchlight.web.routes import auth_routes

    signup(client)
    seen = []

    def record(reset_id, user_id, address, *rest):
        # Only after the response has been produced is the request's transaction committed and visible elsewhere.
        with session_scope() as other:
            seen.append((address, other.get(PasswordReset, reset_id) is not None))

    monkeypatch.setattr(auth_routes, "deliver_password_reset", record)
    forgot(make_client(), OWNER)
    assert seen == [(OWNER, True)]


def test_failed_smtp_delivery_is_logged_with_a_marker_and_masked_address(settings_env, caplog, monkeypatch):
    class BrokenSMTP:
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("connection refused")

    settings_env(**{**PRODUCTION_SMTP, "BATCHLIGHT_ENV": "test"})
    monkeypatch.setattr(email.smtplib, "SMTP", BrokenSMTP)
    with caplog.at_level("WARNING", logger="batchlight.email"):
        result = email.send_email("person@example.org", "Reset your Batchlight password", "link")
    assert result.status == "failed"
    assert "EMAIL_DELIVERY_FAILED" in caplog.text  # counted by the CloudWatch metric filter and alarm
    assert "p***@example.org" in caplog.text
    assert "person@example.org" not in caplog.text


# --------------------------------------------------------------------------- invitations say what really happened


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("sent", "Invitation emailed to tech@example.org."),
        ("skipped", "Email is not set up, so nothing was sent."),
        ("failed", "couldn't send the invitation email"),
    ],
)
def test_invitation_message_matches_the_delivery_result(client, monkeypatch, status, expected):
    from batchlight.web.routes import workspace

    signup(client)
    monkeypatch.setattr(workspace, "send_email", lambda *a, **k: SendResult(status, None if status == "sent" else "x"))
    response = post_form(client, "/app/team/invite", {"email": "tech@example.org", "role": "member"})
    assert response.status_code == 200
    assert expected in html.unescape(response.text)
    assert ("Invitation emailed" in response.text) == (status == "sent")  # never claimed unless it went out
    assert "/invite/" in response.text  # the link is always shown so the inviter can pass it on
