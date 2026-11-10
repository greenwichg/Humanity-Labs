#!/usr/bin/env python3
"""Stop a Terraform plan that would delete or replace anything holding data.

Usage: terraform show -json plan.tfplan | python3 infra/scripts/plan_guard.py
Set ALLOW_DATA_DELETION=true to let such a plan through (the deprovision workflow does this only after approval).
"""

import json
import os
import sys

PROTECTED = {
    "aws_db_instance": "the database",
    "aws_secretsmanager_secret": "a secret",
    "aws_s3_bucket": "an S3 bucket",
    "aws_ecr_repository": "the image repository",
}


def main() -> int:
    plan = json.load(sys.stdin)
    risky = []
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        if "delete" in actions and change.get("type") in PROTECTED:
            how = "replace" if "create" in actions else "delete"
            risky.append(f"{how} {change['address']} ({PROTECTED[change['type']]})")
    if not risky:
        print("plan guard: no data-holding resource is deleted or replaced")
        return 0
    print("plan guard: this plan would " + "; ".join(risky))
    if os.environ.get("ALLOW_DATA_DELETION") == "true":
        print("plan guard: allowed because ALLOW_DATA_DELETION=true")
        return 0
    print("plan guard: refusing. Re-run with explicit approval if this is intended.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
