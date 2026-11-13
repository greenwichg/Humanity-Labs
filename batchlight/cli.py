"""Operational commands.

python -m batchlight.cli make-curator someone@example.org
python -m batchlight.cli ingest [--source us-fda-drug]
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import select

from .db import session_scope
from .models import User


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="batchlight")
    sub = parser.add_subparsers(dest="command", required=True)
    curator = sub.add_parser("make-curator", help="grant the curator role to an existing user")
    curator.add_argument("email")
    curator.add_argument("--revoke", action="store_true")
    sub.add_parser("rescore", help="re-apply the current matching rules to open matches")
    sub.add_parser(
        "seed-india", aliases=["seed-food-india"], help="load the hand-checked Indian medicine and food alerts (idempotent)"
    )
    sub.add_parser("db-check", help="read-only: print the schema revision and row counts (used to verify restores)")
    sub.add_parser(
        "email-check",
        help="check production email settings and that the SMTP login works, without sending anything (run before deploys)",
    )
    infra = sub.add_parser("infra-ingest", help="refresh public infrastructure sources now")
    infra.add_argument("--source", action="append", help="infra-wb-projects, infra-wb-civil-works or infra-cag-reports")
    infra.add_argument("--cag-pages", type=int, default=None, help="how many pages of the CAG report list to read")
    models = sub.add_parser("community-models", help="download or check the pinned vision models for community photos")
    models.add_argument("action", choices=["download", "status", "verify"])
    process = sub.add_parser("community-process", help="blur, assess and publish community photos waiting in processing")
    process.add_argument("--limit", type=int, default=50)
    process.add_argument("--retry-failed", action="store_true", help="also retry posts whose processing failed")
    bounds = sub.add_parser(
        "community-boundaries", help="load Assembly and Lok Sabha constituency boundaries (DataMeet, CC BY 4.0)"
    )
    bounds.add_argument("--file", help="a local copy of the GeoJSON; by default it is downloaded")
    roster = sub.add_parser("community-roster", help="load sitting MPs and MLAs from a CSV that names a source for every row")
    roster.add_argument("file")
    sub.add_parser("community-retag", help="re-tag representatives and re-check official links for all published posts")
    sub.add_parser("community-notes", help="recompute community note statuses from all ratings")
    ingest = sub.add_parser("ingest", help="run ingestion once")
    ingest.add_argument("--source", action="append")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.command == "make-curator":
        with session_scope() as session:
            user = session.scalar(select(User).where(User.email == args.email.lower()))
            if user is None:
                print(f"No user with email {args.email}", file=sys.stderr)
                return 1
            user.is_curator = not args.revoke
            print(f"{user.email} is {'no longer ' if args.revoke else ''}a curator")
        return 0
    if args.command in ("seed-india", "seed-food-india"):
        from .services.india_seed import seed_india_alerts

        with session_scope() as session:
            created, updated, matched = seed_india_alerts(session)
        print(f"Indian alerts: {created} created, {updated} updated, {matched} new stock matches")
        return 0
    if args.command == "email-check":
        from .config import get_settings
        from .services.email import check_smtp_login, production_email_problems

        settings = get_settings()
        problems = production_email_problems(settings)
        for problem in problems:
            print(f"email-check: {problem}")
        if problems:
            return 1
        error = check_smtp_login(settings)
        if error:
            print(f"email-check: SMTP login to {settings.smtp_host}:{settings.smtp_port} failed: {error}")
            return 1
        print(f"email-check: SMTP login to {settings.smtp_host}:{settings.smtp_port} over STARTTLS works; nothing was sent.")
        return 0
    if args.command == "db-check":
        import json

        from sqlalchemy import text

        tables = ("users", "organizations", "alerts", "matches", "food_reports", "infra_projects", "infra_parties")
        with session_scope() as session:
            revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            counts = {t: session.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in tables}  # noqa: S608 - fixed names
            server = session.execute(text("SHOW server_version")).scalar()
        print(json.dumps({"revision": revision, "server_version": server, "rows": counts}))
        return 0
    if args.command == "infra-ingest":
        from .db import session_factory
        from .infra.sources import REGISTRY
        from .infra.store import run_infra_source

        if args.cag_pages:
            REGISTRY["infra-cag-reports"].pages = args.cag_pages
        failed = False
        for source_id in args.source or list(REGISTRY):
            adapter = REGISTRY.get(source_id)
            if adapter is None:
                print(f"{source_id}: unknown source", file=sys.stderr)
                failed = True
                continue
            run = run_infra_source(session_factory(), adapter)
            print(f"{source_id}: {run.status}, fetched {run.fetched}, new {run.created}, updated {run.updated}")
            failed = failed or run.status != "succeeded"
        return 1 if failed else 0
    if args.command == "rescore":
        from .matching import rescore_open_matches

        with session_scope() as session:
            updated, removed = rescore_open_matches(session)
        print(f"open matches updated: {updated}, removed: {removed}")
        return 0
    if args.command == "ingest":
        from .worker import run_once

        failed = False
        for source_id, outcome in run_once(args.source).items():
            print(f"{source_id}: {outcome}")
            failed |= outcome.startswith("failed")
        return 1 if failed else 0
    if args.command.startswith("community-"):
        return community_command(args)
    return 2


def community_command(args) -> int:
    import json

    import httpx

    from .community import notes, pipeline, projects, reps
    from .community.models import CommunityPost
    from .community.vision import models as vision_models
    from .db import session_factory

    if args.command == "community-models":
        if args.action == "download":
            fetched = vision_models.download()
            print(f"downloaded {len(fetched)} file(s); all models ready" if vision_models.ready() else "incomplete")
        elif args.action == "status":
            for key, state in vision_models.status().items():
                print(f"{key}: {state}")
        else:
            bad = vision_models.verify()
            print("all model files match their pinned SHA-256" if not bad else "mismatch: " + ", ".join(bad))
            return 1 if bad else 0
        return 0 if vision_models.ready() else 1
    if args.command == "community-process":
        if args.retry_failed:
            with session_scope() as session:
                session.query(CommunityPost).filter(
                    CommunityPost.status == "processing", CommunityPost.analysis_status == "failed"
                ).update({"analysis_status": "pending"})
        print(pipeline.process_pending(session_scope, limit=args.limit))
        return 0
    if args.command == "community-boundaries":
        if args.file:
            with open(args.file, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = httpx.get(reps.AC_URL, timeout=300, follow_redirects=True).raise_for_status().json()
        with session_scope() as session:
            print(reps.import_assembly_boundaries(session, data))
        return 0
    if args.command == "community-roster":
        with open(args.file, encoding="utf-8") as f, session_scope() as session:
            print(reps.import_roster(session, f.read()))
        return 0
    if args.command == "community-retag":
        with session_scope() as session:
            posts = session.query(CommunityPost).filter(CommunityPost.status == "published").all()
            for post in posts:
                reps.tag(session, post)
                projects.link(session, post, post.author_id)
            print(f"re-tagged {len(posts)} post(s)")
        return 0
    if args.command == "community-notes":
        with session_scope() as session:
            print(f"{notes.recompute(session)} status change(s)")
        return 0
    _ = session_factory
    return 2


if __name__ == "__main__":
    sys.exit(main())
