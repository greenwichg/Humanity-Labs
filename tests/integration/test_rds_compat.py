"""Amazon RDS compatibility: the RDS master user is not a PostgreSQL superuser, so migrations must work without one.

The test creates a plain role that owns its own database (stricter than the RDS master user, which also has
rds_superuser), runs every migration as that role and reads the result back with the db-check command.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, make_url, text

from tests.conftest import TEST_DB

ROOT = Path(__file__).resolve().parents[2]
ROLE = "batchlight_rds_like"
DATABASE = "batchlight_rds_like"
PASSWORD = "rds-like-test-only"  # noqa: S105 - throwaway local test role


def admin_engine():
    return create_engine(TEST_DB, isolation_level="AUTOCOMMIT")


def drop_everything(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))


def run(args: list[str], url: str) -> subprocess.CompletedProcess:
    env = os.environ | {"BATCHLIGHT_DATABASE_URL": url, "BATCHLIGHT_ENV": "test"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=300)


def test_migrations_and_restore_check_work_without_superuser(database):
    admin = admin_engine()
    drop_everything(admin)
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}' NOSUPERUSER NOCREATEROLE"))
        conn.execute(text(f"CREATE DATABASE {DATABASE} OWNER {ROLE}"))
    url = make_url(TEST_DB).set(username=ROLE, password=PASSWORD, database=DATABASE).render_as_string(hide_password=False)
    try:
        migrated = run(["-m", "alembic", "upgrade", "head"], url)
        assert migrated.returncode == 0, migrated.stderr[-2000:]

        checked = run(["-m", "batchlight.cli", "db-check"], url)
        assert checked.returncode == 0, checked.stderr[-2000:]
        report = json.loads(checked.stdout.strip().splitlines()[-1])
        head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
        assert report["revision"] == head
        assert report["rows"]["users"] == 0
        assert report["server_version"]

        engine = create_engine(url)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar() is False
            extensions = set(conn.execute(text("SELECT extname FROM pg_extension")).scalars())
        engine.dispose()
        assert {"citext", "pgcrypto"} <= extensions
    finally:
        drop_everything(admin)
        admin.dispose()


def test_db_check_reports_revision_and_counts(db, capsys):
    from batchlight.cli import main

    assert main(["db-check"]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert set(report["rows"]) >= {"users", "alerts", "food_reports", "infra_projects"}
    assert report["revision"]
