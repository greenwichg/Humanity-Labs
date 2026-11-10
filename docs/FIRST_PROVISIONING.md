# First AWS provisioning: checklist and commands

Prepared on 10 November 2026 from commit `1719e18`, for review before anything is created. Every command and resource count below was checked against that code:

- **Resource counts:** each stack was planned offline with mocked AWS providers.
- **AWS CLI calls:** checked against the AWS API models.
- **Terraform, scripts and workflows:** checked against their variables, outputs, options and inputs.

Nothing in this document has been run.

Every command is labelled:

- **[READ-ONLY]**: reads AWS, DNS or GitHub and changes nothing. `terraform plan` also takes the normal state lock: a small lock object in the state bucket, removed when the plan finishes.
- **[LOCAL]**: changes files on your machine only.
- **[APPROVAL REQUIRED]**: creates or changes AWS resources, DNS records, GitHub settings or application data. Run it only once you have decided to.

The whole process has three phases:

| Phase | What changes | Who acts | Approval |
|---|---|---|---|
| 0. Email (SES) | 3 DNS records, an SES identity, SMTP credentials (an IAM user) | you, in the AWS console | you |
| 1. Bootstrap | **17 AWS resources** (16 if the account already has GitHub's OIDC provider), the SMTP credentials stored in a secret, and **GitHub settings** (environment protection and 5 repository variables) | you, with `bootstrap.sh` | you, at the prompts |
| 2. Provision | **45 platform + 28 app Terraform resources**: network, database, certificate, load balancer, WAF, containers, DNS records | the **AWS provision** workflow | you, in the `production` environment |

---

## Blockers found while verifying (fix before Phase 2)

These are code issues. This commit changes documentation only, so they are **not fixed yet**. Each needs your approval to change.

1. **The first one-off task cannot start (confirmed).**
   - **What happens:** before migrating, the provision workflow applies only part of the app stack: `terraform apply -target=aws_ecs_cluster.main -target=aws_ecs_task_definition.cli` in `.github/workflows/aws-deploy-core.yml`. It then runs the email check and the migrations as one-off ECS tasks.
   - **What's missing:** Terraform's `-target` includes only what the targets depend on. The task definition depends on the execution role, but not on the role's two permission attachments (`aws_iam_role_policy_attachment.execution` and `aws_iam_role_policy.execution_secrets`). An offline plan of that targeted apply creates the cluster, the task definition, the role, the load balancer and its security group, and **not** those two attachments.
   - **Effect:** the email-check task could not pull its image, read its secrets or write logs. The first provision would stop there, after the platform has been created.
   - **Proposed fix (small):** add `depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.execution_secrets]` to the task definitions in `infra/terraform/app/main.tf`, plus an offline test that plans the targeted apply and checks both attachments are in it.
2. **The permissions boundary may block reading secrets (not verified; precaution).** The ECS execution role carries the `humanity-labs-ecs-boundary` permissions boundary, which allows `secretsmanager:GetSecretValue` but not `kms:Decrypt`. The secrets use the AWS-managed key, whose key policy normally grants decryption through Secrets Manager. Whether a permissions boundary still limits that cannot be confirmed without AWS. The low-risk fix is to add `kms:Decrypt` to the boundary, limited by `kms:ViaService = secretsmanager.ap-south-1.amazonaws.com`. That change belongs to the bootstrap stack, so make it before Phase 1.

---

## 1. Prerequisites

**AWS account**

- [ ] An account **dedicated to this site**, fully activated with a payment method. Accounts billed by AWS India add 18% GST and are billed in INR.
- [ ] `humanitylabs.in` is registered in **this** account. On 10 November its public DNS delegated to four Route 53 name servers, and the zone held only its NS and SOA records.
- [ ] Domain auto-renew is on, and any contact-verification email from Route 53 has been answered.
- [ ] On your machine: AWS CLI v2 signed in as an administrator of that account; Terraform 1.10 or newer (CI uses 1.17.1); `gh` signed in as an admin of `greenwichg/Humanity-Labs`; `python3`.

**Phase 0: Amazon SES**

Do all of this in **Mumbai (`ap-south-1`)**; the console often opens in `us-east-1`, so switch region first. Finish it **before Phase 2**, because every deploy checks it before rolling out.

- [ ] **[APPROVAL REQUIRED: adds 3 DNS records]** *Identities → Create identity → Domain → `humanitylabs.in`*, with Easy DKIM and *Publish DNS records to Route 53* ticked.
- [ ] **[APPROVAL REQUIRED]** *Account dashboard → Request production access*, for transactional email: password resets, invitations and alert digests. Approval can take about a day. Until then SES sends only to verified addresses, and deploys stop at the email check.
- [ ] **[APPROVAL REQUIRED: creates an IAM user]** *SMTP settings → Create SMTP credentials*, in Mumbai. Keep the user name and password ready for `bootstrap.sh` to ask for.
- [ ] Optional, each a DNS change needing your approval: a DMARC TXT record, and a custom MAIL FROM domain (MX and SPF TXT) for better deliverability.

## 2. Read-only checks (any time)

**[READ-ONLY]**
```bash
export AWS_PROFILE=<admin-profile>
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}'
aws route53 list-hosted-zones-by-name --dns-name humanitylabs.in --max-items 1 \
  --query 'HostedZones[].{Id:Id,Name:Name,Private:Config.PrivateZone,Records:ResourceRecordSetCount}'
aws route53domains get-domain-detail --region us-east-1 --domain-name humanitylabs.in \
  --query '{AutoRenew:AutoRenew,Status:StatusList,Expires:ExpirationDate,NS:Nameservers[].Name}'
aws sesv2 get-account --region ap-south-1 \
  --query '{ProductionAccess:ProductionAccessEnabled,Sending:SendingEnabled,Max24h:SendQuota.Max24HourSend}'
aws sesv2 get-email-identity --region ap-south-1 --email-identity humanitylabs.in \
  --query '{Verified:VerifiedForSendingStatus,DKIM:DkimAttributes.Status}'
aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn'
```

Go ahead with Phase 2 only when all of these hold:

- `ProductionAccess` is `true`;
- `Verified` is `true` and `DKIM` is `SUCCESS`;
- `Records` shows 5: NS, SOA and the three DKIM records.

If an ARN ending in `oidc-provider/token.actions.githubusercontent.com` is listed, the account already has GitHub's OIDC provider. Add `-var create_github_oidc_provider=false` to the preview in 3a; `bootstrap.sh` detects this itself.

## 3. Phase 1: bootstrap

### 3a. Preview

**[LOCAL]** downloads the Terraform providers:
```bash
cd infra/terraform/bootstrap
terraform init
```
**[READ-ONLY]** plans against AWS and saves the plan locally:
```bash
terraform plan -var aws_region=ap-south-1 -var domain_name=humanitylabs.in \
  -var alert_email=you@example.org -var monthly_budget_usd=125 -out=bootstrap.tfplan
terraform show bootstrap.tfplan
```
Expect **`Plan: 17 to add, 0 to change, 0 to destroy`**, or 16 with `create_github_oidc_provider=false`. Bootstrap state is local (`infra/terraform/bootstrap/terraform.tfstate`, not in git). The budget is set to **125** because AWS Budgets counts tax: expected spend with GST is about $91–97, so the default 100 would warn every month.

### 3b. The 17 bootstrap resources

| Kind | Resources | Count |
|---|---|---|
| Terraform state | S3 bucket `humanity-labs-tfstate-<account>-ap-south-1`, plus its versioning, encryption, public-access block, ownership, lifecycle and TLS-only bucket policy (settings on the one bucket) | 7 |
| GitHub sign-in | IAM OIDC provider for `token.actions.githubusercontent.com` (skipped if one exists) | 1 |
| Roles | `humanity-labs-github-deploy` role and its policy, `humanity-labs-github-backup` role and its policy, the `humanity-labs-ecs-boundary` permissions boundary | 5 |
| Email credentials | Secrets Manager secret `humanity-labs/smtp`, created **empty** | 1 |
| Alerts | SNS topic `humanity-labs-alerts` and its email subscription | 2 |
| Cost | monthly budget `humanity-labs-monthly` | 1 |

### 3c. Run bootstrap

**[APPROVAL REQUIRED: GitHub settings, 17 AWS resources and a secret value]**
```bash
infra/scripts/bootstrap.sh --alert-email you@example.org --budget 125
```
In order:

1. You type the AWS account ID; it must match the signed-in account.
2. It checks that the `humanitylabs.in` hosted zone is in that account and that you are a repository admin. Nothing changes if either check fails.
3. **GitHub settings:**
   - it creates or updates the `production` environment, with you as the only required reviewer;
   - it **deletes every deployment branch or tag rule except `main`**, and adds the `main` rule if missing;
   - it reads the settings back and stops unless they are exactly that.
4. **AWS:** Terraform shows its plan and creates the 17 resources only after you type `yes`.
5. **Secret value:** it asks for the SES SMTP user name and password (the password isn't shown). It stores them in `humanity-labs/smtp`, passing them on standard input, so they never appear in a command line, file or Terraform state. You can skip this and run the script again later.
6. **GitHub variables:** it checks the environment again, then sets five repository variables: `AWS_REGION`, `DOMAIN_NAME`, `TF_STATE_BUCKET`, `AWS_BACKUP_ROLE_ARN`, and `AWS_DEPLOY_ROLE_ARN` last.

Afterwards:

- Click the subscription confirmation email AWS sends; until then alarms can't reach you.
- Keep a copy of `infra/terraform/bootstrap/terraform.tfstate` somewhere safe.
- Once `AWS_DEPLOY_ROLE_ARN` is set, **every push to `main` that passes CI starts *AWS deploy*, which waits for your approval.** Reject those runs until Phase 2 is ready.

## 4. Phase 2: platform and app provisioning

### 4a. Platform preview (after bootstrap)

**[LOCAL]** reads the state bucket name from the bootstrap state and connects to the state backend; no resources are created:
```bash
BUCKET=$(terraform -chdir=infra/terraform/bootstrap output -raw state_bucket)
cd infra/terraform/platform
terraform init -backend-config="bucket=$BUCKET" -backend-config="region=ap-south-1"
```
**[READ-ONLY]** plans against AWS, taking the normal state lock, and checks the plan with the data-loss guard:
```bash
terraform plan -out=platform.tfplan
terraform show -json platform.tfplan | python3 ../../scripts/plan_guard.py
```
Expect **`Plan: 45 to add, 0 to change, 0 to destroy`**, and `plan guard: no data-holding resource is deleted or replaced`. The plan files are only for your review: git ignores them, nothing uses them later, and you can delete them afterwards. The plan reads the hosted zone and the `humanity-labs/smtp` secret, so it only works after bootstrap.

The app stack cannot be previewed yet: it reads the platform's outputs, which exist only once the platform has been applied. The provision workflow plans and applies it after your approval.

### 4b. Run the provision workflow

**[APPROVAL REQUIRED: creates 73 Terraform resources, DNS records and data]** First fix the blockers above, and check that section 2's SES checks pass.

In GitHub: *Actions → AWS provision → Run workflow*, on branch `main`, with `seed` ticked and `restore_snapshot` empty. Then approve the run in the `production` environment. The command-line equivalent:
```bash
gh workflow run aws-provision.yml --repo greenwichg/Humanity-Labs --ref main -f seed=true
```
You still approve the run in the browser. The job's limit is 90 minutes; RDS creation alone usually takes 10–20.

What the run does, in order (from `.github/workflows/aws-deploy-core.yml`):

1. **Platform:** plans the platform stack, checks the plan with the data-loss guard, then applies it, creating the 45 resources below.
2. **Image:** builds the ARM64 container image and pushes it to the new registry.
3. **Partial app apply:** applies the app stack's cluster and one-off task definition. With its dependencies, this also creates the execution role, the load balancer and its security group early.
4. **Email check:** confirms SES production access and the verified domain, checks the SMTP credentials are stored, then runs an SMTP login test as a one-off task. **The run stops here if email isn't ready**, after the platform has been created, so the database and other resources are already billing.
5. **Data:** runs the database migrations, then loads the hand-checked Indian alerts, each as a one-off task.
6. **Full app apply:** applies the rest of the app stack: services, HTTPS listener, WAF and the DNS records.
7. **Checks:** waits for the services, then runs the smoke test. It checks `/healthz`, `/readyz` and `/` over HTTPS with the real certificate, and the `www` and HTTP redirects.

### 4c. The 45 platform resources

| Kind | Resources | Count |
|---|---|---|
| Network | VPC `10.40.0.0/16`, internet gateway, 2 public and 2 private subnets, 2 route tables and 4 associations, the VPC's default security group (adopted and emptied, not a new one), app and database security groups, 6 security-group rules. No NAT gateway | 21 |
| Database | RDS PostgreSQL 16 `humanity-labs-db` (db.t4g.micro, Single-AZ, 20–100 GB gp3, encrypted, private, deletion protection, 35-day point-in-time recovery), its parameter group (TLS required) and subnet group, 2 log groups for exported database logs, and the database password (a random value held in Terraform state only) | 6 |
| Certificate | ACM certificate for `humanitylabs.in` and `www.humanitylabs.in`, 2 DNS validation records, and a step that waits until it is issued | 4 |
| Secrets | `humanity-labs/database-url` and `humanity-labs/app-secret-key` with their values, and the app key (a random value) | 5 |
| Images and logs | ECR repository (immutable tags) and its lifecycle policy, app log group (30 days) | 3 |
| Monitoring | 5 alarms (database storage, CPU, billed CPU credits, connections, failed email) and 1 log metric filter | 6 |

### 4d. The 28 app resources

| Kind | Resources | Count |
|---|---|---|
| IAM | ECS execution role (with the permissions boundary), its ECS policy attachment, its secrets policy | 3 |
| ECS | cluster, capacity providers (Fargate, Fargate Spot), 3 ARM64 task definitions (web, worker, one-off), web service, worker service (Spot) | 7 |
| Load balancer | load-balancer security group and 4 rules, application load balancer, target group, HTTP listener (redirects to `https://humanitylabs.in`), HTTPS listener (TLS 1.2/1.3), canonical-host rule | 10 |
| WAF | web ACL (IP reputation list, 3,000 requests per 5 minutes per address, known bad inputs, common rules) and its association | 2 |
| DNS | A alias records `humanitylabs.in` and `www.humanitylabs.in` pointing at the load balancer | 2 |
| Monitoring | 4 alarms (site down, app 5xx, load-balancer 5xx, worker not running) | 4 |

Created by the run but not Terraform resources: the container image, the one-off ECS tasks, the database schema and seed data, and RDS automated backups.

## 5. After provisioning

**[READ-ONLY]**
```bash
curl -sSI https://humanitylabs.in/healthz | head -1                   # HTTP/2 200
curl -sSI 'https://www.humanitylabs.in/x?y=1' | grep -i '^location'   # https://humanitylabs.in/x?y=1
curl -sSI http://humanitylabs.in/ | grep -i '^location'               # https://humanitylabs.in/
aws acm list-certificates --region ap-south-1 \
  --query 'CertificateSummaryList[?DomainName==`humanitylabs.in`].Status'
aws cloudwatch describe-alarms --region ap-south-1 --alarm-name-prefix humanity-labs \
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```
Alarms reading `INSUFFICIENT_DATA` at first is normal.

Then:

1. Sign up on the site. This creates your account.
2. **[APPROVAL REQUIRED: runs a one-off task that changes your account's role]** Make yourself a curator with `infra/scripts/run-task.sh python -m batchlight.cli make-curator you@example.org`, after exporting the variables shown under *Run a command in AWS* in [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md#run-a-command-in-aws).
3. **[APPROVAL REQUIRED: creates a temporary database]** Run *AWS restore* in `verify` mode once to prove the backups work. Then delete the restored copy, which has deletion protection and costs about $15 a month while it exists.

## 6. Estimated monthly cost

USD, from the AWS Price List API for Mumbai (`ap-south-1`), retrieved in November 2026. Assumptions: 730 hours, the defaults in the code, low traffic.

| Item | Per month |
|---|---|
| Application Load Balancer: $0.0239/h + $0.008 per capacity-unit-hour (0.1–0.5 units) | $18.03–20.37 |
| RDS db.t4g.micro: $0.021/h, plus 20 GB gp3 at $0.131/GB | $17.95 |
| Public IPv4 ×4 (load balancer ×2, web, worker) at $0.005/h | $14.60 |
| WAF: $5 per web ACL + 4 rules at $1 + $0.60 per million requests (1–3 million) | $9.60–10.80 |
| Worker, Fargate ARM64 0.25 vCPU / 2 GB at on-demand rates. It runs on Spot, which is cheaper, but the Spot price is not published in the price list | up to $8.16 |
| Web, Fargate ARM64 0.25 vCPU / 0.5 GB | $5.30 |
| Secrets Manager ×3 ($1.20), hosted zone ($0.50), image storage ($0.50–1), logs ($1–2) | $3.20–4.70 |
| SES at $0.10 per 1,000 emails; state bucket; SNS email; budget; ACM certificate (free); 9 alarms and 1 metric (within CloudWatch's free 10 each) | about $0.10–0.60 |
| **Total before tax** | **about $77–82** |
| **Total with 18% GST** | **about $91–97** |

Not included: the domain's yearly renewal, and data transfer beyond AWS's free 100 GB a month.

## 7. DNS changes

| When | Who | Records in the `humanitylabs.in` zone | Approval |
|---|---|---|---|
| Phase 0 | you, in the SES console | 3 × CNAME `<token>._domainkey.humanitylabs.in` → `<token>.dkim.amazonses.com` | your console action |
| Phase 2, platform | Terraform | 2 × CNAME `_<hash>.humanitylabs.in` and `_<hash>.www.humanitylabs.in` → `…acm-validations.aws` | the provision run |
| Phase 2, app | Terraform | A (alias) `humanitylabs.in` and `www.humanitylabs.in` → the load balancer | the provision run |

Never touched: the NS and SOA records, the hosted zone itself and the registration. Terraform will not overwrite an existing record of the same name and type; a clash fails the run instead. The deploy role is allowed to change only the domain, `www` and `_*` validation names.

Later removals:

- *AWS deprovision* with scope `app` deletes the two A records, so the site stops resolving.
- A full teardown also deletes the two validation records.

## 8. Risks and unexpected costs

**Ordering and first run**

- **SES first.** The run creates the platform before it reaches the email check. If SES isn't ready, it stops with billable resources already created. Run the checks in section 2 first.
- **Blockers above.** Fix them first. More broadly, this is the first real use of the IAM policies, and a missing permission would stop a run without losing data. I would fix the policy, you would re-run `bootstrap.sh`, then re-run provision. The likeliest spots are ACM tagging, the Route 53 record-name condition, WAF and the permissions boundary.

**Behaviour to know**

- **HSTS.** The app sends `Strict-Transport-Security: max-age=31536000; includeSubDomains`. For a year after a browser's first visit, every subdomain must serve HTTPS in that browser. Mail records aren't affected.
- **Email reputation.** High bounce or complaint rates can make SES pause sending. Resets would then quietly fail; the reset page looks the same either way. The email-failure alarm would notify you, but the app does not process bounce notifications.
- **Single points of failure.** One web task and a Single-AZ database: a zone outage means downtime. If Spot capacity runs short, the worker stops and an alarm fires.
- **Public deploy logs.** They show the account ID, ARNs and endpoints, but no secrets.
- **Bootstrap state.** It is a local file. If it is lost, future changes to the roles, budget or secret container mean re-importing them.

**Costs beyond the estimate**

| Item | Possible extra |
|---|---|
| RDS CPU credits ($0.075 per vCPU-hour above the baseline). The first data load after launch will spike CPU, so the credit alarm may fire in the first days | worst case about $98.55/month (alarmed) |
| Database storage autoscaling to 100 GB | up to +$10.48 |
| Backup storage beyond the free 20 GB | $0.095 per GB-month |
| Load-balancer capacity and WAF requests under heavy traffic | per use |
| A restore-drill database left running (deletion protection, so it must be deleted deliberately) | about $15.33+/month each |
| Snapshots kept after a teardown | storage cost |
| CloudWatch if the free allowance does not apply to the account | +$1.20 |
