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
