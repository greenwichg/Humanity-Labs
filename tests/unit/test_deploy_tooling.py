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


# --------------------------------------------------------------------------- deploy gate: email must work before rollout

yaml = pytest.importorskip("yaml")


def email_gate(tmp_path: Path, production="True", verified="True", check_exit=0, smtp_host="", stored="1"):
    """Run the deploy workflow's email gate step, as GitHub runs it, against fake aws and run-task commands."""
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "aws-deploy-core.yml").read_text())
    script = next(s for s in doc["jobs"]["deploy"]["steps"] if s.get("name", "").startswith("Check email delivery"))["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_aws = bin_dir / "aws"
    fake_aws.write_text(
        f'#!/bin/sh\necho "$@" >> {tmp_path}/aws.log\ncase "$*" in\n'
        f"  *get-account*) echo {production} ;;\n  *get-email-identity*) echo {verified} ;;\n"
        f"  *describe-secret*) echo {stored} ;;\nesac\n"
    )
    scripts = tmp_path / "infra" / "scripts"
    scripts.mkdir(parents=True)
    fake_task = scripts / "run-task.sh"
    fake_task.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/task.log\nexit {check_exit}\n')
    for f in (fake_aws, fake_task):
        f.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TF_VAR_email_from": "Humanity Labs <alerts@humanitylabs.in>",
        "TF_VAR_smtp_host": smtp_host,
        "AWS_REGION": "ap-south-1",
        "PROJECT": "humanity-labs",
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    task_log = (tmp_path / "task.log").read_text() if (tmp_path / "task.log").exists() else ""
    aws_log = (tmp_path / "aws.log").read_text() if (tmp_path / "aws.log").exists() else ""
    return result, aws_log, task_log


def test_email_gate_passes_with_ses_production_access_verified_domain_and_working_login(tmp_path):
    result, aws_log, task_log = email_gate(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "get-email-identity --email-identity humanitylabs.in" in aws_log
    assert "describe-secret --secret-id humanity-labs/smtp" in aws_log
    assert task_log.strip() == "python -m batchlight.cli email-check"


def test_email_gate_stops_when_no_smtp_credentials_are_stored(tmp_path):
    result, _, task_log = email_gate(tmp_path, stored="0")
    assert result.returncode != 0
    assert "No SMTP credentials are stored in humanity-labs/smtp" in result.stdout
    assert task_log == ""  # ECS could not even start the check task without them


def test_email_gate_stops_while_ses_is_in_the_sandbox(tmp_path):
    result, _, task_log = email_gate(tmp_path, production="False")
    assert result.returncode != 0
    assert "still in the sandbox" in result.stdout
    assert task_log == ""


def test_email_gate_stops_when_the_sender_domain_is_not_verified(tmp_path):
    result, _, task_log = email_gate(tmp_path, verified="False")
    assert result.returncode != 0
    assert "humanitylabs.in is not verified" in result.stdout
    assert task_log == ""


def test_email_gate_stops_when_the_smtp_login_fails(tmp_path):
    result, _, task_log = email_gate(tmp_path, check_exit=1)
    assert result.returncode != 0
    assert "email-check" in task_log


def test_email_gate_still_checks_the_login_for_a_non_ses_server(tmp_path):
    result, aws_log, task_log = email_gate(tmp_path, production="False", smtp_host="smtp.example.net")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sesv2" not in aws_log  # SES checks apply only to SES
    assert "email-check" in task_log
