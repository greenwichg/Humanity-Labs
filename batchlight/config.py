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
    # Connections per process: pool size plus overflow. Keep (web processes x tasks + worker + one-off tasks) x both
    # under the database's connection limit.
    db_pool_size: int = 10
    db_max_overflow: int = 10

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
    report_emails_per_address_per_day: int = 3

    # Matching
    match_lookback_years: int = 6

    # Abuse protection
    login_max_failures: int = 8
    login_window_minutes: int = 15

    # Community infrastructure. Uploads are off in production until photo storage there is approved and configured.
    community_uploads: bool | None = None  # None: on outside production, off in production
    media_dir: str = "media"  # local photo storage (development); production storage is designed as S3, not yet enabled
    media_s3_bucket: str = ""  # set by Terraform only once the photo bucket is approved and created
    model_dir: str = ""  # vision models; default ~/.cache/humanity-labs/models
    vision_threads: int = 0  # ONNX Runtime threads; 0 means one per CPU
    community_posts_per_day: int = 20
    community_comments_per_hour: int = 60
    community_notes_per_day: int = 10

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def uploads_enabled(self) -> bool:
        return self.community_uploads if self.community_uploads is not None else not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
