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
    if args.command == "ingest":
        from .worker import run_once

        failed = False
        for source_id, outcome in run_once(args.source).items():
            print(f"{source_id}: {outcome}")
            failed |= outcome.startswith("failed")
        return 1 if failed else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
