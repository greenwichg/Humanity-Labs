"""Email in production: working SMTP is required, and the site never says an email went out when it did not."""

from __future__ import annotations

import html
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


# --------------------------------------------------------------------------- password reset never claims a failed send


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_password_reset_says_so_when_the_email_did_not_go_out(client, make_client, db, monkeypatch, status):
    from batchlight.web.routes import auth_routes

    signup(client)
    monkeypatch.setattr(auth_routes, "send_email", lambda *a, **k: SendResult(status, "smtp down"))
    response = post_form(make_client(), "/forgot-password", {"email": "owner@example.org"})
    assert response.status_code == 503
    text = html.unescape(response.text)
    assert "couldn't send the reset email" in text
    assert "we've sent" not in text
    assert db.scalar(select(func.count()).select_from(PasswordReset)) == 0  # no live link without an email
    assert db.scalar(select(AuditEvent.action).where(AuditEvent.action == "password_reset_email_failed"))


def test_password_reset_link_is_stored_only_after_the_email_is_sent(client, make_client, db):
    signup(client)
    response = post_form(make_client(), "/forgot-password", {"email": "owner@example.org"})
    assert response.status_code == 200 and "we've sent a link" in html.unescape(response.text)
    assert db.scalar(select(func.count()).select_from(PasswordReset)) == 1
    assert len(OUTBOX) == 1


def test_password_reset_rate_limit_sends_nothing_and_says_only_what_is_true(client, make_client, db):
    signup(client)
    other = make_client()
    for _ in range(3):
        post_form(other, "/forgot-password", {"email": "owner@example.org"})
    assert len(OUTBOX) == 3
    response = post_form(other, "/forgot-password", {"email": "owner@example.org"})
    assert len(OUTBOX) == 3  # the fourth request sends nothing
    assert "at most three links an hour" in response.text  # so "use the most recent email" is the honest advice


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
