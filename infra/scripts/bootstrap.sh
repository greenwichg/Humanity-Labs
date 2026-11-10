#!/usr/bin/env bash
# One-time AWS and GitHub setup, run from your own machine (go-live steps 2 and 3 in docs/AWS_DEPLOYMENT.md).
#
#   infra/scripts/bootstrap.sh --alert-email you@example.org [--region ap-south-1] [--budget 100]
#
# In this order, so the AWS roles are never usable without your approval:
#   1. checks you are signed in to the right AWS account (you type its ID to confirm) and are a repository admin;
#   2. protects the GitHub "production" environment: you as required reviewer, deployments from main only;
#   3. runs the bootstrap Terraform stack (Terraform shows its plan and asks before changing anything);
#   4. sets the repository variables the workflows need.
# It is safe to run again. It never deletes anything.
#
# Needs: AWS CLI v2 signed in as an administrator of the AWS account dedicated to this site, Terraform 1.10+,
# GitHub CLI (gh) signed in as an admin of the repository, and python3.
set -euo pipefail

REPO="${REPO:-greenwichg/Humanity-Labs}"
ENVIRONMENT="production"
REGION="ap-south-1"
BUDGET="100"
ALERT_EMAIL=""

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --alert-email) ALERT_EMAIL="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --budget) BUDGET="${2:-}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done
case "$ALERT_EMAIL" in
  ?*@?*.?*) ;;
  *) echo "Give the address for alarms and the cost budget: --alert-email you@example.org" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STACK="$ROOT/infra/terraform/bootstrap"
step() { printf '\n==> %s\n' "$*"; }
for tool in aws terraform gh python3; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done

# ---------------------------------------------------------------- 1. who and where
step "Checking the AWS account"
identity=$(aws sts get-caller-identity --output json)
account=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])' <<<"$identity")
caller=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])' <<<"$identity")
echo "Signed in as $caller"
echo "Account $account, region $REGION"
read -r -p "Type the account ID to confirm this is the account dedicated to this site: " typed
[ "$typed" = "$account" ] || { echo "Account ID did not match; nothing was changed." >&2; exit 1; }

step "Checking GitHub access to $REPO"
login=$(gh api user --jq .login)
user_id=$(gh api user --jq .id)
admin=$(gh api "repos/$REPO" --jq .permissions.admin)
[ "$admin" = "true" ] || { echo "$login is not an admin of $REPO; nothing was changed." >&2; exit 1; }
echo "Signed in to GitHub as $login (admin)"

# ---------------------------------------------------------------- 2. protect the environment first
step "Protecting the GitHub environment '$ENVIRONMENT' (required reviewer: $login; branch: main only)"
python3 - "$user_id" <<'PY' | gh api -X PUT "repos/$REPO/environments/$ENVIRONMENT" --input - >/dev/null
import json, sys
print(json.dumps({
    "reviewers": [{"type": "User", "id": int(sys.argv[1])}],
    "prevent_self_review": False,  # a single maintainer must be able to approve their own runs
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}))
PY
if ! gh api "repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main; then
  gh api -X POST "repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies" -f name=main -f type=branch >/dev/null
fi

verify_environment() {
  gh api "repos/$REPO/environments/$ENVIRONMENT" | python3 -c '
import json, sys
env = json.load(sys.stdin)
rules = {r["type"]: r for r in env.get("protection_rules", [])}
reviewers = rules.get("required_reviewers", {}).get("reviewers", [])
policy = env.get("deployment_branch_policy") or {}
ok = bool(reviewers) and policy.get("custom_branch_policies") is True
print("required reviewers: %d, custom branch policy: %s" % (len(reviewers), policy.get("custom_branch_policies")))
sys.exit(0 if ok else 1)'
}
verify_environment || { echo "The environment is not protected as expected; stopping before AWS." >&2; exit 1; }

# ---------------------------------------------------------------- 3. bootstrap stack
step "Running the bootstrap Terraform stack (review the plan; Terraform asks before applying)"
oidc_args=()
if aws iam list-open-id-connect-providers --output text | grep -q "oidc-provider/token.actions.githubusercontent.com"; then
  echo "This account already has GitHub's OIDC provider; reusing it."
  oidc_args=(-var create_github_oidc_provider=false)
fi
terraform -chdir="$STACK" init -input=false >/dev/null
terraform -chdir="$STACK" apply -var "aws_region=$REGION" -var "alert_email=$ALERT_EMAIL" \
  -var "monthly_budget_usd=$BUDGET" ${oidc_args[@]+"${oidc_args[@]}"}  # empty-array form also works on bash 3.2 (macOS)

deploy_role=$(terraform -chdir="$STACK" output -raw deploy_role_arn)
backup_role=$(terraform -chdir="$STACK" output -raw backup_role_arn)
state_bucket=$(terraform -chdir="$STACK" output -raw state_bucket)

# ---------------------------------------------------------------- 4. variables last
step "Setting the repository variables"
verify_environment >/dev/null || { echo "The environment lost its protection; variables not set." >&2; exit 1; }
gh variable set AWS_REGION --repo "$REPO" --body "$REGION"
gh variable set TF_STATE_BUCKET --repo "$REPO" --body "$state_bucket"
gh variable set AWS_BACKUP_ROLE_ARN --repo "$REPO" --body "$backup_role"
gh variable set AWS_DEPLOY_ROLE_ARN --repo "$REPO" --body "$deploy_role"  # last: this one switches on deploys

step "Done"
cat <<EOF
- AWS has emailed $ALERT_EMAIL a subscription confirmation: click it, or alarms will not reach you.
- Keep a copy of $STACK/terraform.tfstate somewhere safe (it is not in git).
- From now on every push to main that passes CI starts "AWS deploy", which waits for your approval.
  Until HTTPS is set up (docs/AWS_DEPLOYMENT.md, step 4), reject those runs or run "AWS provision" yourself.
- Next: HTTPS and email (step 4), then "AWS provision" and an "AWS restore" verify drill (step 5).
EOF
