"""infra/scripts/bootstrap.sh, run against stub aws, gh and terraform commands that record every call.

The order matters for security: the GitHub environment must be protected before the AWS roles exist and before
the variables that switch on deploys are set.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra" / "scripts" / "bootstrap.sh"
REPO = "greenwichg/Humanity-Labs"

STUB = r"""
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
log = os.environ["STUB_LOG"]
stdin = sys.stdin.read() if ("--input" in args and "-" in args) else ""
with open(log, "a") as f:
    f.write(json.dumps({"cmd": name, "args": args, "stdin": stdin}) + "\n")
joined = " ".join(args)
if name == "aws":
    if args[:2] == ["sts", "get-caller-identity"]:
        print(json.dumps({"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/admin"}))
    elif args[:2] == ["iam", "list-open-id-connect-providers"]:
        print(os.environ.get("STUB_OIDC", ""))
    elif args[:2] == ["route53", "list-hosted-zones-by-name"]:
        zones = [{"Id": "/hostedzone/Z0HUMANITYLABS", "Name": "humanitylabs.in.", "Config": {"PrivateZone": False}}]
        print(json.dumps({"HostedZones": [] if os.environ.get("STUB_NO_ZONE") else zones}))
elif name == "gh":
    if joined == "api user --jq .login":
        print("owner")
    elif joined == "api user --jq .id":
        print("4242")
    elif joined.endswith("--jq .permissions.admin"):
        print(os.environ.get("STUB_ADMIN", "true"))
    elif "deployment-branch-policies --jq" in joined:
        print("")
    elif args[:3] == ["api", "-X", "PUT"] or args[:3] == ["api", "-X", "POST"]:
        print("{}")
    elif args[0] == "api" and args[1].endswith("/environments/production"):
        print(json.dumps({"protection_rules": [{"type": "required_reviewers", "reviewers": [{"type": "User"}]}],
                          "deployment_branch_policy": {"custom_branch_policies": True}}))
elif name == "terraform":
    if "output" in args:
        print({"deploy_role_arn": "arn:aws:iam::111122223333:role/humanity-labs-github-deploy",
               "backup_role_arn": "arn:aws:iam::111122223333:role/humanity-labs-github-backup",
               "state_bucket": "humanity-labs-tfstate-111122223333-ap-south-1"}[args[-1]], end="")
"""


def run(tmp_path: Path, typed_account: str, extra_env: dict | None = None, args: list[str] | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "stub.py"
    stub.write_text(STUB)
    for name in ("aws", "gh", "terraform"):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\nexec(open({str(stub)!r}).read())\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    env = os.environ | {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(log)} | (extra_env or {})
    result = subprocess.run(
        ["bash", str(SCRIPT), *(args if args is not None else ["--alert-email", "alerts@example.org"])],
        input=typed_account + "\n",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def index(calls, predicate) -> int:
    return next(i for i, c in enumerate(calls) if predicate(c))


def is_change(c) -> bool:
    """True for any call that changes GitHub settings or AWS resources."""
    a = c["args"]
    if c["cmd"] == "gh":
        return a[:3] in (["api", "-X", "PUT"], ["api", "-X", "POST"]) or a[:2] == ["variable", "set"]
    return c["cmd"] == "terraform" and "apply" in a


def test_protects_the_environment_before_aws_and_sets_the_deploy_role_last(tmp_path):
    result, calls = run(tmp_path, "111122223333")
    assert result.returncode == 0, result.stderr

    protect = index(calls, lambda c: c["cmd"] == "gh" and c["args"][:3] == ["api", "-X", "PUT"])
    body = json.loads(calls[protect]["stdin"])
    assert body["reviewers"] == [{"type": "User", "id": 4242}]
    assert body["deployment_branch_policy"] == {"protected_branches": False, "custom_branch_policies": True}
    branch = index(calls, lambda c: c["cmd"] == "gh" and c["args"][:3] == ["api", "-X", "POST"])
    assert "name=main" in calls[branch]["args"]

    apply = index(calls, lambda c: c["cmd"] == "terraform" and "apply" in c["args"])
    assert protect < branch < apply
    assert "alert_email=alerts@example.org" in calls[apply]["args"]
    assert "create_github_oidc_provider=false" not in calls[apply]["args"]

    assert "domain_name=humanitylabs.in" in calls[apply]["args"]
    zone_check = index(calls, lambda c: c["cmd"] == "aws" and c["args"][:2] == ["route53", "list-hosted-zones-by-name"])
    assert zone_check < protect

    variables = [c["args"][2] for c in calls if c["cmd"] == "gh" and c["args"][:2] == ["variable", "set"]]
    assert variables == ["AWS_REGION", "DOMAIN_NAME", "TF_STATE_BUCKET", "AWS_BACKUP_ROLE_ARN", "AWS_DEPLOY_ROLE_ARN"]
    first_variable = index(calls, lambda c: c["cmd"] == "gh" and c["args"][:2] == ["variable", "set"])
    assert apply < first_variable


def test_wrong_account_changes_nothing(tmp_path):
    result, calls = run(tmp_path, "999999999999")
    assert result.returncode != 0
    assert "did not match" in result.stderr
    assert not any(is_change(c) for c in calls)


def test_non_admin_changes_nothing(tmp_path):
    result, calls = run(tmp_path, "111122223333", {"STUB_ADMIN": "false"})
    assert result.returncode != 0
    assert not any(is_change(c) for c in calls)


def test_reuses_an_existing_github_oidc_provider(tmp_path):
    existing = "arn:aws:iam::111122223333:oidc-provider/token.actions.githubusercontent.com"
    result, calls = run(tmp_path, "111122223333", {"STUB_OIDC": existing})
    assert result.returncode == 0, result.stderr
    apply = next(c for c in calls if c["cmd"] == "terraform" and "apply" in c["args"])
    assert "create_github_oidc_provider=false" in apply["args"]


def test_requires_an_alert_email(tmp_path):
    result, calls = run(tmp_path, "111122223333", args=[])
    assert result.returncode != 0
    assert calls == []


def test_missing_hosted_zone_changes_nothing(tmp_path):
    result, calls = run(tmp_path, "111122223333", {"STUB_NO_ZONE": "1"})
    assert result.returncode != 0
    assert "No public hosted zone for humanitylabs.in" in result.stderr
    assert not any(is_change(c) for c in calls)
