"""Application settings, read from environment variables (see .env.example)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BATCHLIGHT_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/batchlight"
    base_url: str = "http://localhost:8000"
    secure_cookies: bool = False
    session_days: int = 30
    trust_proxy_headers: bool = False

    # Email
    email_backend: Literal["console", "smtp", "disabled"] = "console"
    email_from: str = "Batchlight <alerts@batchlight.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True

    # Ingestion
    ingest_interval_minutes: int = 60
    ingest_since: date = date(2024, 1, 1)
    ingest_overlap_days: int = 45
    sources: list[str] = Field(
        default_factory=lambda: [
            "us-fda-drug",
            "us-fda-device",
            "uk-mhra",
            "ca-health-canada",
            "who-mpa",
            "us-fda-food",
            "ca-cfia-food",
        ]
    )
    openfda_api_key: str = ""
    mhra_max_items: int = 400
    health_canada_detail_days: int = 120
    cfia_detail_limit: int = 150  # CFIA recall pages read per run, for barcodes and printed codes
    health_canada_detail_limit: int = 150  # alert pages read per run; older alerts are filled in over successive runs
    http_timeout_seconds: float = 30.0
    http_user_agent: str = "Batchlight/0.1 (medicine safety alerts; +https://github.com/greenwichg/Humanity-Labs)"

    # Food Safety module
    secret_key: str = ""  # used to hash reporters' IP addresses for rate limiting; set a long random value in production
    report_limit_per_hour: int = 5

    # Matching
    match_lookback_years: int = 6

    # Abuse protection
    login_max_failures: int = 8
    login_window_minutes: int = 15

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
