"""Load curated India food regulatory actions (batchlight/food/data/india_regulatory_actions.json)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..ingest import ensure_sources, upsert_alert
from ..models import Source
from ..services.curation import CuratedAlertInput

DATA = Path(__file__).parent / "data" / "india_regulatory_actions.json"


def seed_india_food_actions(session: Session) -> tuple[int, int]:
    """Insert or update each action; re-running changes nothing unless the file changed."""
    ensure_sources(session)
    source = session.get(Source, "curated")
    created = updated = 0
    for row in json.loads(DATA.read_text(encoding="utf-8")):
        data = CuratedAlertInput(**row)
        _, was_created, changed = upsert_alert(session, source, data.to_record())
        created += int(was_created)
        updated += int(changed and not was_created)
    session.flush()
    return created, updated
