"""Background worker: refresh every enabled source on a schedule.

python -m batchlight.worker          # loop forever
python -m batchlight.worker --once   # one pass (for cron or a scheduled job)
"""

from __future__ import annotations

import argparse
import logging
import signal
import time

from sqlalchemy import select

from .config import get_settings
from .db import session_factory, session_scope
from .ingest import REGISTRY, ensure_sources, run_source
from .models import Source
from .services.email import production_email_problems

log = logging.getLogger("batchlight.worker")
_stop = False


def run_once(only: list[str] | None = None) -> dict[str, str]:
    with session_scope() as session:
        ensure_sources(session)
        enabled = [s.id for s in session.scalars(select(Source).where(Source.enabled.is_(True), Source.automated.is_(True)))]
    results = {}
    for source_id in only or enabled:
        adapter = REGISTRY.get(source_id)
        if adapter is None or not adapter.automated:
            results[source_id] = "unknown source"
            continue
        try:
            run = run_source(session_factory(), adapter)
            results[source_id] = f"ok: fetched {run.fetched}, new {run.created}, updated {run.updated}, matched {run.matched}"
        except Exception as exc:  # noqa: BLE001 - one source failing must not stop the others
            results[source_id] = f"failed: {exc}"
        if _stop:
            break
    if not only:
        results.update(run_infra_due())
    return results


def run_infra_due() -> dict[str, str]:
    """Public infrastructure sources that are due, each on its own interval."""
    from .infra.store import due_sources, run_infra_source

    with session_scope() as session:
        due = due_sources(session)
    results = {}
    for adapter in due:
        if _stop:
            break
        run = run_infra_source(session_factory(), adapter)
        results[adapter.id] = f"{run.status}: fetched {run.fetched}, new {run.created}, updated {run.updated}" + (
            f" ({run.error})" if run.error else ""
        )
    return results


def process_community() -> None:
    """Blur, assess and publish waiting community photos. Cheap when there are none."""
    if not get_settings().uploads_enabled:
        return
    from .community.pipeline import process_pending

    try:
        counts = process_pending(session_scope, limit=5)
        if counts["published"] or counts["failed"]:
            log.info("community: %s", counts)
    except Exception:  # noqa: BLE001 - community processing must not stop alert ingestion
        log.exception("community processing failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batchlight ingestion worker")
    parser.add_argument("--once", action="store_true", help="run every enabled source once and exit")
    parser.add_argument("--source", action="append", help="limit to a source id (repeatable)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if settings.is_production and (problems := production_email_problems(settings)):
        # The worker sends the alert emails; refuse to run in production without working email.
        raise SystemExit("Email is not set up for production: " + "; ".join(problems))

    def stop(*_):
        global _stop
        _stop = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    interval = get_settings().ingest_interval_minutes * 60
    while True:
        for source_id, outcome in run_once(args.source).items():
            log.info("%s: %s", source_id, outcome)
        if args.once or _stop:
            return
        slept = 0
        while slept < interval and not _stop:
            process_community()
            time.sleep(15)
            slept += 15
        if _stop:
            return


if __name__ == "__main__":
    main()
