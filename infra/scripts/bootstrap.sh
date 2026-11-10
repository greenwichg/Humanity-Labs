#!/usr/bin/env bash
# One-time AWS and GitHub setup, run from your own machine (docs/AWS_DEPLOYMENT.md, steps 1 and 2).
#
#   infra/scripts/bootstrap.sh --alert-email you@example.org [--reviewer github-login] [--domain humanitylabs.in]
#                              [--region ap-south-1] [--budget 100]
#
# In this order, so the AWS roles are never usable without your approval:
#   1. checks you are signed in to the right AWS account (you type its ID to confirm), that the domain's Route 53
#      hosted zone is in that account, and that you are a repository admin;
#   2. protects the GitHub "production" environment: the reviewer (you, unless --reviewer names someone else) is the
#      only required reviewer, and only the main branch may deploy. Any other deployment branch or tag rule on that
#      environment is removed. Then it reads the settings back and stops if they are not exactly that;
#   3. runs the bootstrap Terraform stack (Terraform shows its plan and asks before changing anything);
#   4. asks for the Amazon SES SMTP credentials and stores them in Secrets Manager (or you can add them later);
#   5. checks the environment again, then sets the repository variables the workflows need.
# It is safe to run again. Apart from extra deployment rules on the production environment, it deletes nothing.
#
# Needs: AWS CLI v2 signed in as an administrator of the AWS account dedicated to this site, Terraform 1.10+,
# GitHub CLI (gh) signed in as an admin of the repository, and python3.
set -euo pipefail

REPO="${REPO:-greenwichg/Humanity-Labs}"
ENVIRONMENT="production"
REGION="ap-south-1"
BUDGET="100"
DOMAIN="humanitylabs.in"
ALERT_EMAIL=""
REVIEWER=""

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --alert-email) ALERT_EMAIL="${2:-}"; shift 2 ;;
    --reviewer) REVIEWER="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --budget) BUDGET="${2:-}"; shift 2 ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
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
POLICIES_API="repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies"
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

if [ -n "$DOMAIN" ]; then
  step "Checking the Route 53 hosted zone for $DOMAIN"
  zones=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" --output json)
  zone=$(python3 -c '
import json, sys
domain = sys.argv[1].rstrip(".") + "."
zones = [z for z in json.load(sys.stdin)["HostedZones"] if z["Name"] == domain and not z["Config"].get("PrivateZone")]
print(zones[0]["Id"].split("/")[-1] if len(zones) == 1 else len(zones))' "$DOMAIN" <<<"$zones")
  case "$zone" in
    0) echo "No public hosted zone for $DOMAIN in account $account. See docs/AWS_DEPLOYMENT.md, step 4; nothing was changed." >&2; exit 1 ;;
    [0-9]*) echo "$zone public hosted zones for $DOMAIN in this account; keep one. Nothing was changed." >&2; exit 1 ;;
  esac
  echo "Hosted zone $zone"
fi

step "Checking GitHub access to $REPO"
login=$(gh api user --jq .login)
admin=$(gh api "repos/$REPO" --jq .permissions.admin)
[ "$admin" = "true" ] || { echo "$login is not an admin of $REPO; nothing was changed." >&2; exit 1; }
echo "Signed in to GitHub as $login (admin)"
REVIEWER="${REVIEWER:-$login}"
reviewer_id=$(gh api "users/$REVIEWER" --jq .id)
case "$reviewer_id" in
  ''|*[!0-9]*) echo "Could not find the GitHub user $REVIEWER; nothing was changed." >&2; exit 1 ;;
esac

# ---------------------------------------------------------------- 2. protect the environment first
step "Protecting the GitHub environment '$ENVIRONMENT' (required reviewer: $REVIEWER; branch: main only)"
python3 - "$reviewer_id" <<'PY' | gh api -X PUT "repos/$REPO/environments/$ENVIRONMENT" --input - >/dev/null
import json, sys
print(json.dumps({
    "reviewers": [{"type": "User", "id": int(sys.argv[1])}],  # replaces any other reviewers
    "prevent_self_review": False,  # a single maintainer must be able to approve their own runs
    "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
}))
PY

# Exactly one deployment rule: the branch main. Remove any other branch or tag rule.
list_policies() {
  gh api --paginate "$POLICIES_API" --jq '.branch_policies[] | "\(.id) \(.type // "branch") \(.name)"'
}
have_main=""
while read -r id type name; do
  [ -n "$id" ] || continue
  if [ "$type" = "branch" ] && [ "$name" = "main" ] && [ -z "$have_main" ]; then
    have_main=1
    continue
  fi
  echo "Removing the deployment $type rule '$name' from $ENVIRONMENT: only main may deploy to production."
  gh api -X DELETE "$POLICIES_API/$id" </dev/null >/dev/null
done <<<"$(list_policies)"
if [ -z "$have_main" ]; then
  gh api -X POST "$POLICIES_API" -f name=main -f type=branch </dev/null >/dev/null
fi

# Read the settings back and accept only exactly what was intended.
verify_environment() {
  ENV_JSON="$(gh api "repos/$REPO/environments/$ENVIRONMENT")" POLICIES="$(list_policies)" \
    python3 - "$reviewer_id" "$REVIEWER" <<'PY'
import json, os, sys
want_id, want_login = int(sys.argv[1]), sys.argv[2]
env = json.loads(os.environ["ENV_JSON"])
problems = []
rules = {r.get("type"): r for r in env.get("protection_rules", [])}
reviewers = rules.get("required_reviewers", {}).get("reviewers", [])
found = sorted((r.get("type"), (r.get("reviewer") or {}).get("id")) for r in reviewers)
if found != [("User", want_id)]:
    names = [(r.get("reviewer") or {}).get("login") or (r.get("reviewer") or {}).get("slug") for r in reviewers]
    problems.append(f"required reviewers should be exactly {want_login}, found {names or 'none'}")
policy = env.get("deployment_branch_policy") or {}
if policy.get("protected_branches") is not False or policy.get("custom_branch_policies") is not True:
    problems.append(f"deployment branches should be a custom list, found {policy or 'any branch'}")
rows = [line.split(" ", 2)[1:] for line in os.environ["POLICIES"].splitlines() if line.strip()]
if rows != [["branch", "main"]]:
    problems.append(f"only the branch main should be allowed to deploy, found {[' '.join(r) for r in rows] or 'none'}")
for p in problems:
    print(f"  - {p}", file=sys.stderr)
if not problems:
    print(f"Verified: required reviewer {want_login} only; deployments from the branch main only.")
sys.exit(1 if problems else 0)
PY
}
verify_environment || { echo "The environment is not protected exactly as intended; stopping before AWS." >&2; exit 1; }

# ---------------------------------------------------------------- 3. bootstrap stack
step "Running the bootstrap Terraform stack (review the plan; Terraform asks before applying)"
oidc_args=()
if aws iam list-open-id-connect-providers --output text | grep -q "oidc-provider/token.actions.githubusercontent.com"; then
  echo "This account already has GitHub's OIDC provider; reusing it."
  oidc_args=(-var create_github_oidc_provider=false)
fi
terraform -chdir="$STACK" init -input=false >/dev/null
terraform -chdir="$STACK" apply -var "aws_region=$REGION" -var "alert_email=$ALERT_EMAIL" -var "domain_name=$DOMAIN" \
  -var "monthly_budget_usd=$BUDGET" ${oidc_args[@]+"${oidc_args[@]}"}  # empty-array form also works on bash 3.2 (macOS)

deploy_role=$(terraform -chdir="$STACK" output -raw deploy_role_arn)
backup_role=$(terraform -chdir="$STACK" output -raw backup_role_arn)
state_bucket=$(terraform -chdir="$STACK" output -raw state_bucket)
smtp_secret=$(terraform -chdir="$STACK" output -raw smtp_secret_name)

# ---------------------------------------------------------------- 4. SES SMTP credentials
step "Amazon SES SMTP credentials ($smtp_secret)"
# shellcheck disable=SC2016 # the backticks are a JMESPath literal, not shell
stored=$(aws secretsmanager describe-secret --secret-id "$smtp_secret" --query 'length(VersionIdsToStages || `{}`)' --output text)
if [ "$stored" != "0" ]; then
  echo "Already stored. To replace them, see docs/AWS_DEPLOYMENT.md, step 5."
else
  echo "Create them in the SES console ($REGION): SMTP settings -> Create SMTP credentials."
  read -r -p "SES SMTP user name (leave empty to add it later): " smtp_user
  if [ -n "$smtp_user" ]; then
    read -r -s -p "SES SMTP password (not shown): " smtp_password
    echo
    # Passed on stdin, so the password never appears in a command line, a file or Terraform state.
    SMTP_USER="$smtp_user" SMTP_PASSWORD="$smtp_password" \
      python3 -c 'import json, os; print(json.dumps({"username": os.environ["SMTP_USER"], "password": os.environ["SMTP_PASSWORD"]}))' |
      aws secretsmanager put-secret-value --secret-id "$smtp_secret" --secret-string file:///dev/stdin >/dev/null
    unset smtp_password
    echo "Stored."
  else
    echo "Skipped. Deploys stop at the email check until they are stored; run this script again to add them."
  fi
fi

# ---------------------------------------------------------------- 5. variables last
step "Setting the repository variables"
verify_environment >/dev/null || { echo "The environment is no longer protected exactly as intended; variables not set." >&2; exit 1; }
gh variable set AWS_REGION --repo "$REPO" --body "$REGION"
gh variable set DOMAIN_NAME --repo "$REPO" --body "$DOMAIN"
gh variable set TF_STATE_BUCKET --repo "$REPO" --body "$state_bucket"
gh variable set AWS_BACKUP_ROLE_ARN --repo "$REPO" --body "$backup_role"
gh variable set AWS_DEPLOY_ROLE_ARN --repo "$REPO" --body "$deploy_role"  # last: this one switches on deploys

step "Done"
cat <<EOF
- AWS has emailed $ALERT_EMAIL a subscription confirmation: click it, or alarms will not reach you.
- Keep a copy of $STACK/terraform.tfstate somewhere safe (it is not in git).
- From now on every push to main that passes CI starts "AWS deploy", which waits for $REVIEWER's approval.
- Before approving "AWS provision": SES must have production access and a verified $DOMAIN, and the SMTP
  credentials must be stored (step 5 in docs/AWS_DEPLOYMENT.md). The deploy checks all three before rolling out.
  Provision then creates the certificate, the DNS records and the redirects, and the site goes live at
  https://$DOMAIN. Afterwards, run an "AWS restore" verify drill.
EOF
