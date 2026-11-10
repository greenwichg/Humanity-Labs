"""infra/scripts/bootstrap.sh, run against stateful fake aws, gh and terraform commands.

The fakes keep the GitHub environment (reviewers, branch rules) and the SMTP secret in a JSON file, so the tests can
check what the script leaves behind, not only which commands it ran. Order matters for security: the environment
must be protected exactly as intended before the AWS roles exist and before the variables that switch on deploys.
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
ENV_PATH = f"repos/{REPO}/environments/production"
POLICIES_PATH = f"{ENV_PATH}/deployment-branch-policies"
USERS = {"owner": 4242, "helper": 5151, "someone-else": 9999}

STUB = r"""
import json, os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
state_file = os.environ["STUB_STATE"]
state = json.load(open(state_file))
reads_stdin = ("--input" in args and "-" in args) or "file:///dev/stdin" in args
stdin = sys.stdin.read() if reads_stdin else ""
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps({"cmd": name, "args": args}) + "\n")
users = json.loads(os.environ["STUB_USERS"])
env_path, policies_path = os.environ["STUB_ENV_PATH"], os.environ["STUB_POLICIES_PATH"]

def save():
    json.dump(state, open(state_file, "w"))

if name == "aws":
    if args[:2] == ["sts", "get-caller-identity"]:
        print(json.dumps({"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/admin"}))
    elif args[:2] == ["iam", "list-open-id-connect-providers"]:
        print(os.environ.get("STUB_OIDC", ""))
    elif args[:2] == ["route53", "list-hosted-zones-by-name"]:
        zones = [{"Id": "/hostedzone/Z0HUMANITYLABS", "Name": "humanitylabs.in.", "Config": {"PrivateZone": False}}]
        print(json.dumps({"HostedZones": [] if os.environ.get("STUB_NO_ZONE") else zones}))
    elif args[:2] == ["secretsmanager", "describe-secret"]:
        print(state["secret_versions"])
    elif args[:2] == ["secretsmanager", "put-secret-value"]:
        state["smtp_value"] = json.loads(stdin)
        state["secret_versions"] = 1
        save()
elif name == "gh":
    a = args
    if a == ["api", "user", "--jq", ".login"]:
        print("owner")
    elif a[:1] == ["api"] and a[1].startswith("users/"):
        login = a[1].split("/", 1)[1]
        if login not in users:
            sys.stderr.write("gh: Not Found (HTTP 404)\n")
            sys.exit(1)
        print(users[login])
    elif a[-2:] == ["--jq", ".permissions.admin"]:
        print(os.environ.get("STUB_ADMIN", "true"))
    elif a[:4] == ["api", "-X", "PUT", env_path]:
        body = json.loads(stdin)
        if not os.environ.get("STUB_IGNORE_REVIEWERS"):
            state["reviewers"] = [r["id"] for r in body["reviewers"]]
        if not os.environ.get("STUB_IGNORE_BRANCH_POLICY"):
            state["protected"] = body["deployment_branch_policy"]["protected_branches"]
            state["custom"] = body["deployment_branch_policy"]["custom_branch_policies"]
        save()
        print("{}")
    elif a[:3] == ["api", "-X", "DELETE"] and a[3].startswith(policies_path + "/"):
        if os.environ.get("STUB_DELETE_FAILS"):
            sys.stderr.write("gh: Forbidden (HTTP 403)\n")
            sys.exit(1)
        gone = int(a[3].rsplit("/", 1)[1])
        state["policies"] = [p for p in state["policies"] if p["id"] != gone]
        save()
    elif a[:4] == ["api", "-X", "POST", policies_path]:
        fields = dict(f.split("=", 1) for f in a[4:] if "=" in f)
        state["policies"].append({"id": max([p["id"] for p in state["policies"]] + [0]) + 1, **fields})
        save()
        print("{}")
    elif a[:3] == ["api", "--paginate", policies_path]:
        for p in state["policies"]:
            print(p["id"], p.get("type", "branch"), p["name"])
    elif a == ["api", env_path]:
        logins = {v: k for k, v in users.items()}
        print(json.dumps({
            "protection_rules": [{"type": "required_reviewers", "reviewers": [
                {"type": "User", "reviewer": {"id": r, "login": logins.get(r)}} for r in state["reviewers"]]}],
            "deployment_branch_policy": {"protected_branches": state["protected"], "custom_branch_policies": state["custom"]}
            if state["custom"] is not None else None,
        }))
elif name == "terraform":
    if "output" in args:
        print({"deploy_role_arn": "arn:aws:iam::111122223333:role/humanity-labs-github-deploy",
               "backup_role_arn": "arn:aws:iam::111122223333:role/humanity-labs-github-backup",
               "state_bucket": "humanity-labs-tfstate-111122223333-ap-south-1",
               "smtp_secret_name": "humanity-labs/smtp"}[args[-1]], end="")
"""

FRESH = {"reviewers": [], "protected": None, "custom": None, "policies": [], "secret_versions": 0}


def run(tmp_path: Path, answers: list[str], state: dict | None = None, extra_env: dict | None = None, args=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "stub.py"
    stub.write_text(STUB)
    for name in ("aws", "gh", "terraform"):
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\nexec(open({str(stub)!r}).read())\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(FRESH | (state or {})))
    log = tmp_path / "calls.jsonl"
    env = (
        os.environ
        | {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "STUB_STATE": str(state_file),
            "STUB_USERS": json.dumps(USERS),
            "STUB_ENV_PATH": ENV_PATH,
            "STUB_POLICIES_PATH": POLICIES_PATH,
        }
        | (extra_env or {})
    )
    result = subprocess.run(
        ["bash", str(SCRIPT), *(args if args is not None else ["--alert-email", "alerts@example.org"])],
        input="".join(f"{a}\n" for a in answers),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls, json.loads(state_file.read_text())


ACCOUNT = "111122223333"


def index(calls, predicate) -> int:
    return next(i for i, c in enumerate(calls) if predicate(c))


def is_aws_change(c) -> bool:
    return (c["cmd"] == "terraform" and "apply" in c["args"]) or (c["cmd"] == "aws" and "put-secret-value" in c["args"])


def is_variable(c) -> bool:
    return c["cmd"] == "gh" and c["args"][:2] == ["variable", "set"]


def is_change(c) -> bool:
    a = c["args"]
    github = c["cmd"] == "gh" and (a[:3] in (["api", "-X", "PUT"], ["api", "-X", "POST"], ["api", "-X", "DELETE"]))
    return github or is_aws_change(c) or is_variable(c)


# --------------------------------------------------------------------------- the happy path and its order


def test_protects_the_environment_before_aws_and_sets_the_deploy_role_last(tmp_path):
    result, calls, state = run(tmp_path, [ACCOUNT, ""])
    assert result.returncode == 0, result.stderr

    protect = index(calls, lambda c: c["cmd"] == "gh" and c["args"][:3] == ["api", "-X", "PUT"])
    main_rule = index(calls, lambda c: c["cmd"] == "gh" and c["args"][:3] == ["api", "-X", "POST"])
    verified = index(calls, lambda c: c["cmd"] == "gh" and c["args"] == ["api", ENV_PATH])
    apply = index(calls, lambda c: c["cmd"] == "terraform" and "apply" in c["args"])
    zone_check = index(calls, lambda c: c["cmd"] == "aws" and c["args"][:2] == ["route53", "list-hosted-zones-by-name"])
    first_variable = index(calls, is_variable)
    assert zone_check < protect < main_rule < verified < apply < first_variable

    assert state["reviewers"] == [USERS["owner"]]
    assert (state["protected"], state["custom"]) == (False, True)
    assert [(p["type"], p["name"]) for p in state["policies"]] == [("branch", "main")]
    assert "alert_email=alerts@example.org" in calls[apply]["args"]
    assert "domain_name=humanitylabs.in" in calls[apply]["args"]
    variables = [c["args"][2] for c in calls if is_variable(c)]
    assert variables == ["AWS_REGION", "DOMAIN_NAME", "TF_STATE_BUCKET", "AWS_BACKUP_ROLE_ARN", "AWS_DEPLOY_ROLE_ARN"]
    assert "Verified: required reviewer owner only; deployments from the branch main only." in result.stdout


# --------------------------------------------------------------------------- only main may deploy


def test_removes_every_deployment_rule_except_main(tmp_path):
    existing = [
        {"id": 1, "type": "branch", "name": "develop"},
        {"id": 2, "type": "branch", "name": "main"},
        {"id": 3, "type": "tag", "name": "v*"},
        {"id": 4, "type": "branch", "name": "release/*"},
    ]
    result, calls, state = run(tmp_path, [ACCOUNT, ""], {"policies": existing, "custom": True, "protected": False})
    assert result.returncode == 0, result.stderr
    deleted = sorted(int(c["args"][3].rsplit("/", 1)[1]) for c in calls if c["args"][:3] == ["api", "-X", "DELETE"])
    assert deleted == [1, 3, 4]
    assert not any(c["args"][:3] == ["api", "-X", "POST"] for c in calls)  # main already existed
    assert [(p["type"], p["name"]) for p in state["policies"]] == [("branch", "main")]
    assert "Removing the deployment tag rule 'v*'" in result.stdout


def test_a_tag_named_main_does_not_count_as_the_main_branch(tmp_path):
    result, _, state = run(tmp_path, [ACCOUNT, ""], {"policies": [{"id": 7, "type": "tag", "name": "main"}]})
    assert result.returncode == 0, result.stderr
    assert [(p["type"], p["name"]) for p in state["policies"]] == [("branch", "main")]


def test_stops_before_aws_if_an_extra_rule_cannot_be_removed(tmp_path):
    result, calls, _ = run(
        tmp_path, [ACCOUNT, ""], {"policies": [{"id": 1, "type": "branch", "name": "develop"}]}, {"STUB_DELETE_FAILS": "1"}
    )
    assert result.returncode != 0
    assert not any(is_aws_change(c) or is_variable(c) for c in calls)


def test_stops_before_aws_if_github_keeps_allowing_any_branch(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT, ""], {"protected": False, "custom": None}, {"STUB_IGNORE_BRANCH_POLICY": "1"})
    assert result.returncode != 0
    assert "deployment branches should be a custom list" in result.stderr
    assert not any(is_aws_change(c) or is_variable(c) for c in calls)


# --------------------------------------------------------------------------- exactly the intended reviewer


def test_stops_before_aws_if_the_reviewer_is_not_exactly_the_intended_one(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT, ""], {"reviewers": [USERS["someone-else"]]}, {"STUB_IGNORE_REVIEWERS": "1"})
    assert result.returncode != 0
    assert "required reviewers should be exactly owner, found ['someone-else']" in result.stderr
    assert not any(is_aws_change(c) or is_variable(c) for c in calls)


def test_stops_before_aws_if_an_extra_reviewer_remains(tmp_path):
    result, calls, _ = run(
        tmp_path, [ACCOUNT, ""], {"reviewers": [USERS["owner"], USERS["someone-else"]]}, {"STUB_IGNORE_REVIEWERS": "1"}
    )
    assert result.returncode != 0
    assert "required reviewers should be exactly owner, found ['owner', 'someone-else']" in result.stderr
    assert not any(is_aws_change(c) or is_variable(c) for c in calls)


def test_replaces_other_reviewers_with_the_named_reviewer(tmp_path):
    result, _, state = run(
        tmp_path,
        [ACCOUNT, ""],
        {"reviewers": [USERS["owner"], USERS["someone-else"]]},
        args=["--alert-email", "alerts@example.org", "--reviewer", "helper"],
    )
    assert result.returncode == 0, result.stderr
    assert state["reviewers"] == [USERS["helper"]]
    assert "required reviewer helper only" in result.stdout


def test_unknown_reviewer_changes_nothing(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT, ""], args=["--alert-email", "alerts@example.org", "--reviewer", "ghost"])
    assert result.returncode != 0
    assert not any(is_change(c) for c in calls)


# --------------------------------------------------------------------------- SMTP credentials


def test_stores_smtp_credentials_through_stdin_only(tmp_path):
    result, calls, state = run(tmp_path, [ACCOUNT, "AKIASMTPUSER", "smtp-pass-123"])
    assert result.returncode == 0, result.stderr
    assert state["smtp_value"] == {"username": "AKIASMTPUSER", "password": "smtp-pass-123"}
    assert not any("smtp-pass-123" in " ".join(c["args"]) for c in calls)  # never on a command line
    assert "smtp-pass-123" not in result.stdout + result.stderr
    put = index(calls, lambda c: c["cmd"] == "aws" and "put-secret-value" in c["args"])
    assert put < index(calls, is_variable)


def test_smtp_credentials_can_be_added_later(tmp_path):
    result, calls, state = run(tmp_path, [ACCOUNT, ""])
    assert result.returncode == 0, result.stderr
    assert "Skipped. Deploys stop at the email check" in result.stdout
    assert "smtp_value" not in state


def test_stored_smtp_credentials_are_not_asked_for_again(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT], {"secret_versions": 1})
    assert result.returncode == 0, result.stderr
    assert "Already stored" in result.stdout
    assert not any("put-secret-value" in c["args"] for c in calls)


# --------------------------------------------------------------------------- refusals before any change


def test_wrong_account_changes_nothing(tmp_path):
    result, calls, _ = run(tmp_path, ["999999999999"])
    assert result.returncode != 0
    assert "did not match" in result.stderr
    assert not any(is_change(c) for c in calls)


def test_non_admin_changes_nothing(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT], extra_env={"STUB_ADMIN": "false"})
    assert result.returncode != 0
    assert not any(is_change(c) for c in calls)


def test_missing_hosted_zone_changes_nothing(tmp_path):
    result, calls, _ = run(tmp_path, [ACCOUNT], extra_env={"STUB_NO_ZONE": "1"})
    assert result.returncode != 0
    assert "No public hosted zone for humanitylabs.in" in result.stderr
    assert not any(is_change(c) for c in calls)


def test_reuses_an_existing_github_oidc_provider(tmp_path):
    existing = "arn:aws:iam::111122223333:oidc-provider/token.actions.githubusercontent.com"
    result, calls, _ = run(tmp_path, [ACCOUNT, ""], extra_env={"STUB_OIDC": existing})
    assert result.returncode == 0, result.stderr
    apply = next(c for c in calls if c["cmd"] == "terraform" and "apply" in c["args"])
    assert "create_github_oidc_provider=false" in apply["args"]


def test_requires_an_alert_email(tmp_path):
    result, calls, _ = run(tmp_path, [], args=[])
    assert result.returncode != 0
    assert calls == []
