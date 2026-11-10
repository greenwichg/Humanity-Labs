"""The small scripts the AWS workflows rely on: the container entry point and the Terraform plan guard."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRINT_URL = ["sh", "-c", 'printf %s "$BATCHLIGHT_DATABASE_URL"']


def entrypoint_url(**env: str) -> str:
    base = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "entrypoint.sh"), *PRINT_URL],
        env=base | env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_entrypoint_swaps_only_the_database_host():
    url = "postgresql+psycopg://batchlight:s3cret%2Fpw@old-db.abc.ap-south-1.rds.amazonaws.com:5432/batchlight?sslmode=require"
    out = entrypoint_url(
        BATCHLIGHT_DATABASE_URL=url,
        BATCHLIGHT_DATABASE_HOST_OVERRIDE="restore-db.abc.ap-south-1.rds.amazonaws.com",
    )
    assert out == (
        "postgresql+psycopg://batchlight:s3cret%2Fpw@restore-db.abc.ap-south-1.rds.amazonaws.com:5432/batchlight?sslmode=require"
    )


def test_entrypoint_keeps_a_url_without_port():
    out = entrypoint_url(BATCHLIGHT_DATABASE_URL="postgresql+psycopg://u:p@old/db", BATCHLIGHT_DATABASE_HOST_OVERRIDE="new")
    assert out == "postgresql+psycopg://u:p@new/db"


def test_entrypoint_leaves_the_url_alone_without_override():
    url = "postgresql+psycopg://u:p@db:5432/batchlight"
    assert entrypoint_url(BATCHLIGHT_DATABASE_URL=url) == url


def change(address: str, kind: str, actions: list[str]) -> dict:
    return {"address": address, "type": kind, "change": {"actions": actions}}


def guard(changes: list[dict], allow: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ALLOW_DATA_DELETION", None)
    if allow:
        env["ALLOW_DATA_DELETION"] = "true"
    return subprocess.run(
        [sys.executable, str(ROOT / "infra" / "scripts" / "plan_guard.py")],
        input=json.dumps({"resource_changes": changes}),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("kind", "actions"),
    [
        ("aws_db_instance", ["delete", "create"]),
        ("aws_db_instance", ["create", "delete"]),
        ("aws_db_instance", ["delete"]),
        ("aws_secretsmanager_secret", ["delete"]),
        ("aws_s3_bucket", ["delete", "create"]),
        ("aws_ecr_repository", ["delete"]),
    ],
)
def test_plan_guard_stops_data_loss(kind, actions):
    result = guard([change(f"{kind}.main", kind, actions)])
    assert result.returncode == 1
    assert f"{kind}.main" in result.stdout


def test_plan_guard_needs_explicit_permission_to_continue():
    result = guard([change("aws_db_instance.main", "aws_db_instance", ["delete"])], allow=True)
    assert result.returncode == 0
    assert "allowed because ALLOW_DATA_DELETION=true" in result.stdout


def test_plan_guard_lets_ordinary_changes_through():
    result = guard(
        [
            change("aws_db_instance.main", "aws_db_instance", ["update"]),
            change("aws_lb.web", "aws_lb", ["delete", "create"]),
            change("aws_ecs_service.web", "aws_ecs_service", ["delete"]),
            change("aws_secretsmanager_secret_version.app", "aws_secretsmanager_secret_version", ["no-op"]),
        ]
    )
    assert result.returncode == 0, result.stdout
