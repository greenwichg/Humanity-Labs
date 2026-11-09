"""Outbound email. The console backend logs messages, for development."""

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
