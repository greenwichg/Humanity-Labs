"""Load the hand-checked Indian medicine and food alerts in batchlight/data/india_alerts.json.

Each row declares its evidence (official notice, official lab result or news report) and is
validated like an alert added in the curator console. Medicine rows are matched against
workspace stock and the workspaces notified, exactly as when a curator publishes an alert.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from .curation import CuratedAlertInput, publish_curated

DATA = Path(__file__).resolve().parent.parent / "data" / "india_alerts.json"


def load_rows(path: Path = DATA) -> list[CuratedAlertInput]:
    return [CuratedAlertInput(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def seed_india_alerts(session: Session) -> tuple[int, int, int]:
    """Insert or update each alert. Returns (created, updated, new stock matches). Safe to re-run."""
    created = updated = matched = 0
    for data in load_rows():
        _, was_created, changed, new_matches = publish_curated(session, data)
        created += int(was_created)
        updated += int(changed and not was_created)
        matched += new_matches
    return created, updated, matched
