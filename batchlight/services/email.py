"""Outbound email. The console backend logs messages for development; in production it logs only the envelope."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from ..config import get_settings

log = logging.getLogger("batchlight.email")
OUTBOX: list[EmailMessage] = []  # console backend keeps the last messages for tests and debugging


@dataclass
class SendResult:
    status: str  # sent | skipped | failed
    error: str | None = None


PLACEHOLDER_PASSWORDS = {"", "not-set", "changeme", "change-me"}


def production_email_problems(settings) -> list[str]:
    """What stops email from working in production. Password resets and invitations depend on it, so production
    refuses to start (web and worker) and deploys stop (cli email-check) until this list is empty."""
    problems = []
    if settings.email_backend != "smtp":
        problems.append(f"BATCHLIGHT_EMAIL_BACKEND must be smtp in production, not {settings.email_backend!r}")
        return problems
    if not settings.smtp_host or settings.smtp_host in {"localhost", "127.0.0.1"}:
        problems.append("BATCHLIGHT_SMTP_HOST must name your SMTP service, for example email-smtp.ap-south-1.amazonaws.com")
    if not settings.smtp_username:
        problems.append("BATCHLIGHT_SMTP_USERNAME is empty")
    if settings.smtp_password.strip().lower() in PLACEHOLDER_PASSWORDS:
        problems.append("BATCHLIGHT_SMTP_PASSWORD is empty or still a placeholder")
    if not settings.smtp_starttls:
        problems.append(
            "BATCHLIGHT_SMTP_STARTTLS must be true: credentials and reset links must not cross the network unencrypted"
        )
    if "@" not in settings.email_from:
        problems.append("BATCHLIGHT_EMAIL_FROM must contain a sender address")
    return problems


def check_smtp_login(settings) -> str | None:
    """Connect, upgrade to TLS and log in, without sending anything. Returns what went wrong, or None."""
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(settings.smtp_username, settings.smtp_password)
            code, _ = smtp.noop()
            if code != 250:
                return f"the server answered {code} after login"
    except smtplib.SMTPAuthenticationError as exc:
        return f"the SMTP user name or password was refused ({exc.smtp_code})"
    except (OSError, smtplib.SMTPException) as exc:
        return f"{type(exc).__name__}: {exc}"[:300]
    return None


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def send_email(to: str, subject: str, text: str) -> SendResult:
    settings = get_settings()
    if settings.email_backend == "disabled":
        return SendResult("skipped", "Email delivery is disabled")
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = " ".join(subject.split())[:250]  # names are user input: no line breaks in headers
    message.set_content(text)
    if settings.email_backend == "console":
        if settings.is_production:
            # Nothing is delivered, and bodies hold sign-in, reset and invitation links, so log only the envelope.
            log.warning("email not sent (console backend in production) to=%s subject=%s", _mask(to), subject)
            return SendResult("skipped", "Email is not configured (console backend)")
        OUTBOX.append(message)
        del OUTBOX[:-100]
        log.info("email (console backend) to=%s subject=%s\n%s", to, subject, text)
        return SendResult("sent")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return SendResult("sent")
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("email to %s failed: %s", to, exc)
        return SendResult("failed", str(exc)[:500])
