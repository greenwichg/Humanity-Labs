"""Source adapters and the ingestion pipeline. Importing this package registers every adapter."""

from . import cfia, fda, healthcanada, mhra, who  # noqa: F401  (registers adapters)
from .base import REGISTRY, AlertRecord, SourceAdapter, ensure_sources, run_source, upsert_alert  # noqa: F401
