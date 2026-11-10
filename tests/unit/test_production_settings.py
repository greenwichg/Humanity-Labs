"""Production behaviour that the AWS deployment relies on."""

from __future__ import annotations

import logging

import pytest

from batchlight.config import get_settings
from batchlight.services import email


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("BATCHLIGHT_ENV", "production")
    monkeypatch.setenv("BATCHLIGHT_EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_console_email_in_production_is_not_sent_and_hides_the_body(production, caplog):
    before = len(email.OUTBOX)
    body = "Reset your password: https://example.org/reset-password/SECRET-TOKEN"
    with caplog.at_level(logging.INFO, logger="batchlight.email"):
        result = email.send_email("person@example.org", "Reset your password", body)
    assert result.status == "skipped"  # callers then show the link on screen instead of claiming it was emailed
    assert "SECRET-TOKEN" not in caplog.text
    assert "person@example.org" not in caplog.text
    assert "p***@example.org" in caplog.text
    assert len(email.OUTBOX) == before


def test_console_email_outside_production_still_logs_for_development(monkeypatch, caplog):
    monkeypatch.setenv("BATCHLIGHT_ENV", "development")
    monkeypatch.setenv("BATCHLIGHT_EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.INFO, logger="batchlight.email"):
            result = email.send_email("dev@example.org", "Hello", "body text")
        assert result.status == "sent"
        assert "body text" in caplog.text
    finally:
        get_settings.cache_clear()


def test_pool_size_comes_from_settings(monkeypatch):
    monkeypatch.setenv("BATCHLIGHT_DB_POOL_SIZE", "5")
    monkeypatch.setenv("BATCHLIGHT_DB_MAX_OVERFLOW", "3")
    get_settings.cache_clear()
    from batchlight import db

    try:
        db.reset_engine()
        engine = db.get_engine()
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 3
    finally:
        db.reset_engine()
        get_settings.cache_clear()
