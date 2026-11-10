# Running Humanity Labs on AWS

This guide covers the Terraform setup in `infra/terraform` and the GitHub Actions workflows that provision, update, back up, restore and tear down the site on AWS. The general application settings are in [DEPLOYMENT.md](DEPLOYMENT.md).

**In short:** the site runs at **https://humanitylabs.in** (`www` redirects there). It is one small web container and one worker container on ECS Fargate (ARM64/Graviton), behind AWS WAF and an Application Load Balancer, with Amazon RDS for PostgreSQL in private subnets, in the Mumbai region (`ap-south-1`). It costs about **$77–82 a month** at low traffic, before tax, or about $67–72 without WAF ([estimate](#estimated-monthly-cost)). Every workflow that changes AWS waits for your approval. Database backups survive every teardown option.

## What the application needs

These facts come from inspecting the repository; nothing about existing AWS resources was assumed.

| Need | Where it comes from |
|---|---|
| One container image with three modes: `web` (port 8000), `worker`, `migrate` | `Dockerfile`, `deploy/entrypoint.sh` |
| PostgreSQL 14+ with `citext` and `pgcrypto`, generated `tsvector` columns, GIN indexes, `websearch_to_tsquery`, JSONB and arrays | `migrations/versions/*` |
| No files on local disk: uploads (CSV imports) are read into the database | `batchlight/web/routes/*` |
| Exactly one worker (it runs scheduled ingestion); the PMGSY import peaked at about 1.1 GB of memory | measured locally |
| Outbound HTTPS to regulator and government sites; outbound SMTP for email | `batchlight/ingest`, `batchlight/infra`, `batchlight/services/email.py` |
| `BATCHLIGHT_SECRET_KEY` of at least 32 characters in production | `batchlight/web/app.py` |
| `/healthz` (process up) and `/readyz` (database reachable) | `batchlight/web` |

All state is in PostgreSQL, so backing up the database backs up the site.

## Database choice: Amazon RDS for PostgreSQL

RDS for PostgreSQL 16 fits, and it is what this setup uses. These checks back that up:

- **Superuser not needed.** The RDS master user is not a PostgreSQL superuser. `tests/integration/test_rds_compat.py` creates a plain role that owns its database (stricter than the RDS master user), runs every migration as that role, checks that `citext` and `pgcrypto` were created, and reads the result back with `batchlight.cli db-check`. It passes on PostgreSQL 16. Both extensions are on RDS's supported list and are "trusted", so a database owner may create them.
- **Full test suite on PostgreSQL 16**, the same major version Terraform requests (`db_engine_version = "16"`).
- **TLS enforced.** The parameter group sets `rds.force_ssl = 1`, and the connection URL Terraform writes uses `sslmode=require`. psycopg 3 supports both.
- **Size.** The local database with all sources loaded is about 1.5 GB. RDS starts at 20 GB of gp3 storage and grows automatically up to 100 GB.

Options considered and not chosen:

- **Aurora PostgreSQL.** Its smallest provisioned instance class (db.t4g.medium) is larger than this app needs. The serverless option bills per capacity unit, and the hourly worker keeps it from pausing.
- **PostgreSQL on EC2 or in a container.** Cheaper on paper, but you would own backups, point-in-time recovery, patching and failover yourself.

The default instance is **db.t4g.micro** (2 vCPU burstable, 1 GiB memory, Single-AZ). If searches slow down as data grows, set `db_instance_class = "db.t4g.small"` (2 GiB, twice the instance price). For automatic failover to a second zone, set `db_multi_az = true` (roughly doubles the database cost).

## Architecture

```
humanitylabs.in, www.humanitylabs.in ──► Route 53 (alias records)
                 ▼
Internet ──► AWS WAF (rate limit per address, AWS managed rules)
                 ▼
           Application Load Balancer (public subnets, ports 80/443)
                 │  only the load balancer may reach the tasks
                 ▼
           ECS Fargate, ARM64: web (1 task)    worker (1 task, Fargate Spot)    one-off tasks (migrate, seed, checks)
                 │  tasks have public addresses for OUTBOUND traffic only (no NAT gateway)
                 ▼
           RDS PostgreSQL 16 (private subnets, no internet route, TLS only, encrypted)
Secrets Manager: database URL, app secret key, SES SMTP credentials      ECR: images      CloudWatch Logs: 30 days
ACM: certificate for humanitylabs.in and www.humanitylabs.in (DNS-validated, renewed automatically)
```

Terraform is split into three stacks so the disposable parts can be removed without touching data:

| Stack | Holds | State | Removed by |
|---|---|---|---|
| `infra/terraform/bootstrap` | S3 bucket for Terraform state (versioned, encrypted, TLS only), GitHub OIDC provider, two IAM roles for GitHub Actions and the permissions boundary for roles they create, the alert topic and a monthly cost budget | Local file in that folder (run by hand) | Never by a workflow |
| `infra/terraform/platform` | VPC and subnets, security groups, **RDS database**, secrets, ECR repository, log groups, the ACM certificate and its validation records | S3 `platform/terraform.tfstate` | Only "AWS deprovision" with scope `everything-except-backups` |
| `infra/terraform/app` | ECS cluster, task definitions, web and worker services, load balancer, HTTPS listener and redirects, WAF, the DNS records for the domain and `www` | S3 `app/terraform.tfstate` | "AWS deprovision" (either scope) |

Security choices:

- **No stored AWS keys.** GitHub Actions gets short-lived credentials through OIDC. The deploy role trusts only jobs of `greenwichg/Humanity-Labs` running in the `production` environment. The backup role trusts only the `main` branch and can only create, tag and delete its own `*-daily-*` snapshots.
- **A deploy role that cannot become an administrator or destroy backups.** It manages the network, ECS, RDS and the load balancer, but:
  - it can create only roles named `<project>-ecs-*`, and only with the `<project>-ecs-boundary` permissions boundary (image pulls, logs, this project's secrets);
  - it can attach only the ECS task execution policy, pass roles only to ECS tasks, and cannot change its own role or the backup role;
  - it can read only this project's secrets;
  - explicit denies stop it from deleting or sharing snapshots, deleting retained automated backups, exporting snapshots, or sharing secrets, images or logs with another account.

  - in DNS it may change only the records it manages (the domain, `www` and certificate validation) in the domain's hosted zone, so it cannot touch mail or other records.

  It still has broad EC2, RDS and load-balancer rights, so **use an AWS account dedicated to this site** (for example a member account in AWS Organizations), not one shared with other systems.
- **No exposed database.** The database has no public address, and its security group admits port 5432 only from the app's security group.
- **Load balancer only.** Tasks accept traffic only from the load balancer. The load balancer drops invalid headers, and with a certificate it redirects HTTP to HTTPS (TLS 1.2/1.3 policy).
- **WAF in front.** AWS WAF blocks:
  - addresses on Amazon's IP reputation list;
  - any address sending more than 3,000 requests in 5 minutes. The limit is generous because many Indian mobile users share addresses behind carrier-grade NAT;
  - known exploit inputs, and requests matched by the AWS common rule set. The common set's request-body size rule only counts, so CSV imports still work.

  Turn it off with `enable_waf = false` in the app stack, and tune the limit with `waf_rate_limit`.
- **Secrets stay out of task definitions.** ECS reads them from Secrets Manager when a task starts.
- **Encryption and scanning.** Storage, snapshots, state and images are encrypted. ECR scans images on push, and its tags are immutable.

## One-time setup

You need:

- an AWS account dedicated to this site, which holds the `humanitylabs.in` domain registration in Route 53 (see [The domain](#4-the-domain-humanitylabsin) if the domain is in a different account);
- AWS CLI v2 signed in as an administrator of that account, on your own machine;
- Terraform 1.10 or newer (the workflows use 1.17.1);
- the GitHub CLI (`gh`) signed in as an admin of the repository.

**Quickest route for steps 1 and 2:** one command that does both, in the safe order:

```bash
infra/scripts/bootstrap.sh --alert-email you@example.org     # optional: --reviewer <login> --region ap-south-1 --budget 100
```

It asks you to type the AWS account ID to confirm the account, and checks that the `humanitylabs.in` hosted zone is in that account (`--domain` changes the domain). Then it:

1. protects the `production` environment: you (or `--reviewer`) as the **only** required reviewer, and the branch `main` as the **only** allowed deployment rule. It removes any other branch or tag rule. Then it reads the settings back and stops before AWS unless they are exactly that;
2. runs the bootstrap stack (Terraform shows its plan and asks before changing anything);
3. asks for the Amazon SES SMTP user name and password (step 5) and stores them in the secret `humanity-labs/smtp`. The password is not shown, and is passed on standard input, so it never lands in a command line, file or Terraform state. You can skip this and run the script again later;
4. checks the environment again, then sets `AWS_REGION`, `DOMAIN_NAME`, `TF_STATE_BUCKET` and `AWS_BACKUP_ROLE_ARN`, and `AWS_DEPLOY_ROLE_ARN` last, which switches on deploys.

It stops before changing anything if the account ID doesn't match, the hosted zone is missing, the reviewer doesn't exist, or you aren't a repository admin. It is safe to run again. The manual equivalent follows.

### 1. Bootstrap (from your machine, once)

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply -var alert_email=you@example.org
#   review: 1 S3 bucket, 1 OIDC provider, 2 IAM roles and their policies, 1 permissions boundary,
#           1 SNS alert topic with your email subscription, 1 monthly budget
terraform output         # note state_bucket, deploy_role_arn, backup_role_arn
```

- **Confirm the alerts.** AWS emails a subscription confirmation to `alert_email`. Alarms reach you only after you click it.
- **Budget.** The budget is $100 a month by default. It emails at 80% of actual spend and at 100% of forecast spend. Change it with `-var monthly_budget_usd=...`. Without `alert_email`, no budget or subscription is created.
- **Updates.** If you pull changes to this stack later, run `terraform apply` here again with the same variables.

- If the account already has a GitHub OIDC provider (`token.actions.githubusercontent.com`), add `-var create_github_oidc_provider=false`.
- To use another region, add `-var aws_region=...` here and set the `AWS_REGION` variable below.
- The repository name in the trust policy is case-sensitive and must be `greenwichg/Humanity-Labs`.
- This stack's state is the local file `terraform.tfstate`, which git ignores. Keep a copy somewhere safe, for example a password manager. If it is lost, re-import the bucket and roles with `terraform import`.

### 2. GitHub settings

**Environment.** Settings → Environments → New environment `production`:

- Add yourself as the required reviewer. Every workflow that changes AWS waits here for approval.
- Under "Deployment branches and tags", choose *Selected branches and tags* and keep exactly one rule: the branch `main`. Delete any other branch or tag rule. The deploy role trusts any job in this environment, so another allowed branch would let its workflows deploy.
- Do this **before** setting `AWS_DEPLOY_ROLE_ARN`. GitHub creates an environment with no protection the first time a workflow uses it, and Terraform cannot enforce these settings.

**Variables.** Settings → Secrets and variables → Actions → Variables (no secrets are needed):

| Variable | Value | Required |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `deploy_role_arn` from step 1 | yes |
| `AWS_BACKUP_ROLE_ARN` | `backup_role_arn` from step 1 | yes, for backups |
| `TF_STATE_BUCKET` | `state_bucket` from step 1 | yes |
| `AWS_REGION` | default `ap-south-1` | no |
| `PROJECT` | default `humanity-labs` (names every resource) | no |
| `DOMAIN_NAME` | default `humanitylabs.in`; set only to use another domain registered in Route 53 in the same account | no |
| `EMAIL_FROM` | default `Humanity Labs <alerts@humanitylabs.in>` | no |
| `SMTP_HOST` | default Amazon SES in the region (`email-smtp.ap-south-1.amazonaws.com`) | no |
| `CPU_ARCHITECTURE` | default `ARM64` (Graviton); set `X86_64` to switch back | no |
| `DB_IDENTIFIER` | leave unset; only for [adopting a restored database](#adopt-a-promoted-database-into-terraform) | no |

Until `AWS_DEPLOY_ROLE_ARN` exists, the automatic deploy and the backup workflows skip themselves.

### 3. First deployment

**Do step 5 (email) first.** Before migrating or rolling anything out, every deploy checks three things:

- SES has production access;
- the sender domain is verified;
- the SMTP login works from inside AWS. Nothing is sent.

If any check fails, the deploy stops and leaves the running site alone.

Actions → **AWS provision** → Run workflow (leave `seed` ticked) → approve the run under `production`.

The workflow:

1. Creates the network, database, secrets and image repository.
2. Builds the image for this commit and pushes it to ECR.
3. Runs the database migrations as a one-off task, then loads the hand-checked Indian alerts.
4. Starts the web and worker services and waits for them to settle.
5. Checks `/healthz`, `/readyz` and `/`.

RDS creation alone usually takes several minutes. On the first run, Terraform also requests the certificate and waits for ACM to issue it, usually a few minutes. The run summary shows the site address, https://humanitylabs.in.

The smoke test connects straight to the load balancer while presenting the real host name, so it checks the certificate even before DNS has spread. It also checks that `www` and plain HTTP redirect to `https://humanitylabs.in` with a 301.

Then:

1. Sign up through the site.
2. Make yourself a curator by [running a command in AWS](#run-a-command-in-aws): `infra/scripts/run-task.sh python -m batchlight.cli make-curator you@example.org`
3. Follow the remaining steps in [DEPLOYMENT.md → After the first deploy](DEPLOYMENT.md#after-the-first-deploy). The worker refreshes all sources on its schedule; `python -m batchlight.cli infra-ingest` starts the public-works import immediately.

> The site is HTTPS-only from the first deploy, with Secure cookies and HSTS. Do not invite real users before email works (step 5).

### 4. The domain (humanitylabs.in)

`humanitylabs.in` is registered in Amazon Route 53. Registering it there created its public hosted zone, and Terraform handles everything else on the next approved **AWS provision** or **AWS deploy**:

| What | Where | Detail |
|---|---|---|
| Hosted zone | Route 53 (created at registration) | Looked up only. Terraform never creates, changes or deletes it, and no teardown removes it |
| Certificate | ACM, `ap-south-1` (platform stack) | One certificate for `humanitylabs.in` and `www.humanitylabs.in`, validated by two CNAME records Terraform adds to the zone. ACM renews it automatically while those records exist |
| `humanitylabs.in` | A alias → load balancer (app stack) | Queries to alias records are free |
| `www.humanitylabs.in` | A alias → load balancer | Answered with a 301 to `https://humanitylabs.in`, keeping the path and query |
| `http://` (either name) | Load balancer port 80 | 301 straight to `https://humanitylabs.in` in one hop |
| Any other host, such as the load balancer's own name | HTTPS listener | 301 to `https://humanitylabs.in` |
| App settings | Task definitions | `BATCHLIGHT_BASE_URL=https://humanitylabs.in` (used in email links), Secure cookies, HSTS |

**State on 10 November 2026**, from public DNS and certificate-transparency logs (nothing in AWS was read or changed):

- The `.in` registry delegates the domain to the Route 53 name servers `ns-104.awsdns-13.com`, `ns-546.awsdns-04.net`, `ns-1594.awsdns-07.co.uk` and `ns-1499.awsdns-59.org`, so the registration is complete and the hosted zone answers.
- The zone holds only its NS and SOA records:
  - no A record for `humanitylabs.in`;
  - `www.humanitylabs.in` does not exist;
  - no CAA, MX or TXT records.
- No certificate has been issued for the domain, which is expected before the first provision.
- DNSSEC is off: there is no DS record at the registry.

**What remains to make the domain live:**

1. The domain must be in the **same AWS account** as the deployment, because Terraform looks the hosted zone up there. If it was bought in another account, either:
   - create a public hosted zone for `humanitylabs.in` in the deployment account, then point the domain at it: in the account that holds the registration, open *Registered domains → humanitylabs.in → Edit name servers* and enter the new zone's four name servers. The registration can stay where it is; or
   - also transfer the registration (*Registered domains → Actions → Transfer to another AWS account*). Hosted zones don't move between accounts, so you still create the zone in the deployment account and point the name servers at it as above.
2. Run `infra/scripts/bootstrap.sh`. Among other things, it lets the deploy role manage the domain's records and certificate.
3. Approve **AWS provision**. When it finishes, https://humanitylabs.in is live. No manual certificate or DNS step is needed.
4. In Route 53 → *Registered domains*, check that automatic renewal is on and the contact details are correct.

Optional hardening, each a separate DNS change that needs your approval:

- **CAA record.** `0 issue "amazon.com"` stops other certificate authorities issuing certificates for the domain.
- **DNSSEC.** Signing needs a KMS key, at about $1 a month.
- **HSTS preload.** Add the domain to the browser preload list once HTTPS has run without trouble for a while.

### 5. Email (manual)

Password resets, invitations and alert emails depend on email, so production requires working SMTP:

- **Startup:** the web and worker processes refuse to start in production unless email is set to SMTP with a real host, user name and password, STARTTLS, and a sender address.
- **Deploys:** every deploy checks, before rolling anything out, that SES has production access, the sender domain is verified, and the SMTP login works (`python -m batchlight.cli email-check`, which sends nothing).
- **Honest results:** the site never says an email went out when it didn't:
  - if a reset email fails, the page says so and asks the user to try again, and no reset link is stored;
  - an invitation says whether it was emailed, or that sending failed, and always shows the link to pass on.

Set it up **before the first provision**, in Amazon SES in `ap-south-1`:

1. **Verify the domain.** *Identities → Create identity → Domain → humanitylabs.in*, with Easy DKIM, and tick *Publish DNS records to Route 53*. This adds three DKIM CNAME records to the hosted zone, a live DNS change you make yourself; the deploy role is not allowed to touch those records. Verification usually completes within minutes once the records exist.
2. **Request production access.** *Account dashboard → Request production access* (transactional mail: password resets, invitations, alert digests). A new SES account can only send to verified addresses until AWS approves this, which can take about a day.
3. **Create SMTP credentials.** *SMTP settings → Create SMTP credentials*. This creates an IAM user, and shows the SMTP user name and password once.
4. **Store them.** Run `infra/scripts/bootstrap.sh` (it asks for them), or put them yourself:

   ```bash
   read -r -p "SMTP user name: " u; read -r -s -p "SMTP password: " p; echo
   U="$u" P="$p" python3 -c 'import json,os; print(json.dumps({"username": os.environ["U"], "password": os.environ["P"]}))' \
     | aws secretsmanager put-secret-value --secret-id humanity-labs/smtp --secret-string file:///dev/stdin
   ```

   Use the same command to replace them later (for example after rotating the SMTP credentials), then run **AWS deploy** so the tasks pick them up.

Mail goes out from `Humanity Labs <alerts@humanitylabs.in>`; set `EMAIL_FROM` to change it. The address must be on a verified domain.

The secret `humanity-labs/smtp` belongs to the bootstrap stack, so it exists before the first deploy and survives every teardown. The SMTP user name stays out of the public deploy logs.

Not wired up: `BATCHLIGHT_OPENFDA_API_KEY` (optional, raises openFDA rate limits). To use it, add a secret to `infra/terraform/platform/secrets.tf` the same way as the app secret key.

## The workflows

| Workflow | When | What it does | Approval |
|---|---|---|---|
| **CI** (`ci.yml`) | every push and pull request | tests, lint, image build; Terraform format, validate and Checkov scan | none (changes nothing) |
| **AWS provision** | by hand | first deployment, or a rebuild after a teardown (optionally from a snapshot) | `production` |
| **AWS deploy** | after CI passes on `main`, or by hand | builds the image (once per commit), applies Terraform changes to both stacks, migrates, then rolls out | `production` |
| **AWS backup** | daily at 03:00 IST, or by hand | snapshots the database, checks point-in-time recovery, removes its own snapshots past retention | none (narrow backup role) |
| **AWS restore** | by hand | restores a snapshot or a point in time to a **new** database, checks it, and with `promote` switches the app to it | `production` + typed confirmation for `promote` |
| **AWS deprovision** | by hand | removes the app layer, or everything except backups | `production` + typed confirmation |

Production safeguards:

- **One run at a time.** The production workflows share one concurrency group.
- **Plan guard.** Every Terraform plan for the platform stack passes through `infra/scripts/plan_guard.py`. It stops the run if the plan would delete or replace the database, a secret, an S3 bucket or the image repository. **AWS deploy** has an `allow_data_deletion` checkbox for an intended replacement. It is off by default and the run still needs approval.
- **Migrations before rollout.** Migrations run before the new web and worker tasks start. If the new web tasks fail their health checks, ECS rolls back to the previous version (deployment circuit breaker).
- **The approval comes before the plan.** GitHub asks for approval when the job starts, before Terraform has made its plan. To read a plan first, run `terraform plan` locally in `infra/terraform/platform` or `infra/terraform/app`. Planning only reads AWS.

### Updating the infrastructure

Edit the Terraform under `infra/terraform/platform` or `infra/terraform/app` and push to `main`. CI checks formatting and validity, and **AWS deploy** applies the change after your approval. Settings worth knowing:

| Setting | Stack | Default |
|---|---|---|
| `web_count`, `web_cpu`, `web_memory`, `web_concurrency` | app | 1 task, 0.25 vCPU, 512 MB, 2 processes |
| `worker_cpu`, `worker_memory`, `worker_use_spot` | app | 0.25 vCPU, 2 GB, Spot |
| `cpu_architecture` | app | `ARM64` (set by the `CPU_ARCHITECTURE` variable; the image is built to match) |
| `db_pool_size`, `db_max_overflow` | app | 5 and 5 connections per process. That's at most 60 in total during a deploy, below the 70-connection alarm |
| `enable_waf`, `waf_rate_limit` | app | on, 3,000 requests per 5 minutes per address |
| `db_instance_class`, `db_multi_az`, `db_allocated_storage`, `db_max_allocated_storage` | platform | db.t4g.micro, Single-AZ, 20 GB, up to 100 GB |
| `db_backup_retention_days` | platform | 35 (point-in-time recovery window, the RDS maximum) |
| `log_retention_days`, `image_retention_count` | platform | 30 days, 30 images |

## Monitoring and alerts

Eight CloudWatch alarms email the address you gave the bootstrap stack. Eight alarms stay within CloudWatch's free tier of ten.

| Alarm | Fires when |
|---|---|
| `<project>-web-down` | no healthy web task behind the load balancer for 3 minutes |
| `<project>-web-5xx` | more than 10 server errors from the app in 5 minutes |
| `<project>-load-balancer-5xx` | more than 10 errors from the load balancer itself in 5 minutes |
| `<project>-worker-not-running` | no worker task for 15 minutes (for example no Fargate Spot capacity); data refreshes and alert emails stop |
| `<project>-db-storage-low` | under 2 GiB of free database storage |
| `<project>-db-cpu-high` | database CPU above 80% for 15 minutes |
| `<project>-db-cpu-surplus-charged` | the burstable database is spending CPU credits that AWS bills |
| `<project>-db-connections-high` | more than 70 database connections |

Other notices:

- **Cost.** The monthly budget emails at 80% of actual spend and at 100% of forecast spend.
- **Failed GitHub runs.** GitHub emails failures of scheduled workflows (the daily backup) to the person who last changed the schedule, and failures of other runs to whoever started them.

## Backups

| Backup | Kept | Survives deleting the database? |
|---|---|---|
| RDS automated backups: daily snapshot plus transaction logs, so any second in the window can be restored. Run by RDS itself | 35 days | Yes. `delete_automated_backups = false` keeps them for the rest of their retention period |
| Daily snapshot from **AWS backup** (`<project>-daily-<db>-<time>`) | 35 days, never fewer than the 7 newest | Yes. Manual snapshots stay until deleted |
| Snapshot taken by **AWS deprovision** before it removes anything (`<project>-predeprovision-<time>`) | until you delete it | Yes |
| RDS final snapshot when the database is deleted by a full teardown (`<project>-final-<time>`) | until you delete it | Yes |
| Terraform state (S3 versioning) | old versions 90 days | Yes |

The backup role can delete only snapshots it created itself (tag `created-by=github-backup`). No workflow deletes the other snapshots, and the deploy role is explicitly denied deleting or sharing any snapshot.

The daily snapshot workflow is a second layer. GitHub turns off scheduled workflows in a public repository after 60 days without activity in it, so do not rely on it alone. The RDS automated backups above do not depend on GitHub.

Not covered: copies in another region or another AWS account, which would protect against losing the whole region or a compromised account. To add them, copy snapshots with `aws rds copy-db-snapshot`, or use AWS Backup with a cross-region rule. Both add storage cost.

**Test a restore regularly**, for example monthly (next section). A backup you have never restored is not proven.

## Recovery runbooks

### Run a command in AWS

One-off commands use the CLI task definition and `infra/scripts/run-task.sh`, from your machine with administrator credentials:

```bash
cd infra/terraform/app
terraform init -backend-config="bucket=<TF_STATE_BUCKET>" -backend-config="region=ap-south-1"
export CLUSTER=$(terraform output -raw cluster_name)
export TASK_DEFINITION=$(terraform output -raw cli_task_definition)
export SUBNETS=$(terraform output -json subnet_ids | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)))')
export SECURITY_GROUP=$(terraform output -raw app_security_group_id)
export LOG_GROUP=/ecs/humanity-labs
cd ../../..
infra/scripts/run-task.sh python -m batchlight.cli db-check
```

### Check that a backup restores (no effect on the live site)

Actions → **AWS restore**, `source` = `latest` (or a snapshot name, or a UTC time such as `2026-11-10T03:00:00Z`), `mode` = `verify`.

The workflow:

1. Creates a **new** instance `<project>-restore-<time>` in the same private subnets.
2. Runs `db-check` against it. This prints the schema revision and row counts and changes nothing.
3. Leaves the live database and the app alone.

Afterwards, delete the copy, which costs money while it runs:

```bash
aws rds modify-db-instance --db-instance-identifier <restore-name> --no-deletion-protection --apply-immediately
aws rds delete-db-instance --db-instance-identifier <restore-name> --skip-final-snapshot
```

### Undo bad data or a bad migration (the database still exists)

1. Run **AWS restore** with `source` set to a UTC time just before the problem, and `mode` = `verify`. Compare the row counts in the run log.
2. If it looks right, run it again with `mode` = `promote` and `confirm` = `promote restored database`. Or run once with `promote`. The workflow then:
   - gives the restored database the same point-in-time recovery window (35 days), storage autoscaling and daily snapshots as the managed one (it is restored on gp3 storage, like the original);
   - points the `database-url` secret at it;
   - restarts web and worker.
3. The old database is **not** changed or deleted. Anything written after the restore time exists only there. Copy it across by hand if needed.
4. Later deploys keep using the restored database, because Terraform does not overwrite the secret. To manage it with Terraform again, adopt it (next section). Until then, the old database also stays and keeps costing money.

If a migration in the release was the problem, also revert the commit before the next deploy, or the migration runs again.

### Adopt a promoted database into Terraform

Do this with administrator credentials once the restored database has proven itself.

```bash
cd infra/terraform/platform
terraform init -backend-config="bucket=<TF_STATE_BUCKET>" -backend-config="region=ap-south-1"
terraform state rm aws_db_instance.main
terraform import -var db_identifier=<restore-name> aws_db_instance.main <restore-name>
```

Then:

1. Set the GitHub variable `DB_IDENTIFIER=<restore-name>` and run **AWS deploy**.
2. Check the plan in the log. It should only update the instance in place (log exports, final-snapshot name, tags). The plan guard stops the run if anything would replace it.
3. Delete the old database yourself when you no longer need it. It keeps a final snapshot:

```bash
aws rds modify-db-instance --db-instance-identifier humanity-labs-db --no-deletion-protection --apply-immediately
aws rds delete-db-instance --db-instance-identifier humanity-labs-db --final-db-snapshot-identifier humanity-labs-db-retired-<date>
```

### Rebuild after a full teardown

Run **AWS provision** with `restore_snapshot` set to the final snapshot named in the deprovision summary. Any snapshot works; list them with:

```bash
aws rds describe-db-snapshots --snapshot-type manual --query "DBSnapshots[].DBSnapshotIdentifier"
```

Terraform creates the database from that snapshot and sets a new master password, and the app's database URL secret is created to match.

The SES SMTP credentials survive, because their secret belongs to the bootstrap stack. One secret does not come back:

- **The app secret key** is regenerated. It only keys the hash used for the hourly food-report rate limit, so the only effect is that the current hour's limits reset.

### The database was deleted outside these workflows

Deletion protection makes this unlikely. If it happens:

1. Run **AWS provision** with `restore_snapshot` set to the newest snapshot. The plan creates the database again from it.
2. For a point in time after that snapshot, use the retained automated backups, then adopt the result as above:

```bash
aws rds describe-db-instance-automated-backups --query "DBInstanceAutomatedBackups[].[DBInstanceIdentifier,DbiResourceId,RestoreWindow]"
aws rds restore-db-instance-to-point-in-time --source-dbi-resource-id <DbiResourceId> --restore-time <UTC time> --target-db-instance-identifier <new-name> ...
```

### Lost Terraform state

The state bucket is versioned. In the S3 console, open `platform/terraform.tfstate` or `app/terraform.tfstate`, choose "Show versions", and restore the last good version.

## Deprovisioning

Actions → **AWS deprovision**. Type `deprovision production` and approve. Both scopes first take a snapshot `<project>-predeprovision-<time>`.

| Scope | Removes | Keeps | Bring it back |
|---|---|---|---|
| `app` (default) | load balancer, ECS services and tasks, task definitions, cluster, WAF, the `humanitylabs.in` and `www` records (the site stops resolving) | database, all backups, secrets, images, network, logs, the certificate, the hosted zone and the domain | **AWS provision** (all data intact) |
| `everything-except-backups` | the above plus the database (after an RDS final snapshot), network, secrets, image repository, logs, and the certificate with its validation records | every snapshot, the retained automated backups, the Terraform state bucket, the IAM roles, the hosted zone and the domain registration | **AWS provision** with `restore_snapshot` |

The full scope stops before removing anything if another database, such as an un-adopted restore, still uses the network. No workflow deletes snapshots other than the daily ones past retention.

Cost while torn down:

- With scope `app`, the database, its storage and the secrets keep running: roughly $19/month.
- With the full scope, only snapshot storage remains, at $0.095 per GB-month.

To remove the GitHub roles and state bucket too, run `terraform destroy` in `infra/terraform/bootstrap` by hand. The bucket has `prevent_destroy`, which you must remove first, on purpose.

## Estimated monthly cost

Prices are for `ap-south-1` from the AWS Price List API, retrieved in November 2026, in USD before tax. Accounts billed by AWS India add 18% GST. Assumptions: 730 hours a month, the defaults above, low traffic.

| Item | Calculation | Per month |
|---|---|---|
| Web task (0.25 vCPU, 0.5 GB, ARM64) | $0.02383/vCPU-h, $0.00261/GB-h (x86 would be $0.04256 and $0.004655) | $5.30 |
| Worker task (0.25 vCPU, 2 GB, ARM64) | same rates, on-demand | up to $8.16 |
| Application Load Balancer | $0.0239/h + $0.008 per LCU-hour (about 0.1–0.5 LCU at low traffic) | $18.03–20.37 |
| Public IPv4 addresses (load balancer ×2, web, worker) | 4 × $0.005/h | $14.60 |
| Route 53 hosted zone for humanitylabs.in | $0.50 per zone; alias queries free, others $0.40 per million | about $0.50 |
| ACM certificate | public certificates are free | $0 |
| RDS db.t4g.micro, Single-AZ | $0.021/h | $15.33 |
| RDS gp3 storage, 20 GB | $0.131/GB-month | $2.62 |
| Backup storage (35 days of point-in-time recovery plus daily snapshots) | free up to the database's allocated storage (20 GB) while it exists, then $0.095/GB-month | $0 to start |
| RDS CPU credits | db.t4g instances always run in Unlimited mode: CPU above the baseline is billed at $0.075 per vCPU-hour | $0 at low load; up to about $99 if the database ran at full CPU all month |
| Alarms, alert emails, budget | 8 alarms (CloudWatch's free tier covers 10), SNS email, one budget | $0 |
| AWS WAF | $5 per web ACL + $1 per rule (4 rules) + $0.60 per million requests | about $9.60–10.80 |
| Secrets Manager, 3 secrets | $0.40 each + $0.05 per 10,000 reads | $1.20 |
| ECR image storage | $0.10/GB-month, at most 30 images kept | about $0.50–1 |
| CloudWatch Logs | $0.67/GB ingested, $0.03/GB-month stored, 30 days | about $1–2 |
| Terraform state bucket, one-off tasks | | under $0.50 |
| **Total** | | **about $77–82, or $91–97 with 18% GST** (about $67–72 without WAF) |

The domain's yearly registration fee is billed separately by Route 53 and is not included.

Notes:

- **Worker on Spot.** The worker runs on Fargate Spot by default (ARM64 tasks have been supported on Spot since September 2024), which is cheaper than the on-demand rate in the table. The Mumbai Spot price could not be verified, so the table shows on-demand as the upper bound.
- **ARM64.** Graviton saves about $10.57 a month against x86 at on-demand rates. The image is built for ARM64 under emulation in GitHub Actions, and CI builds it on every push.
- **Data transfer.** Data sent out to the internet beyond AWS's free monthly allowance is billed per GB. A low-traffic site stays inside it.
- **What was left out to save money:**
  - a NAT gateway (billed per hour and per GB; the tasks use public addresses for outbound traffic instead);
  - Multi-AZ (would add about $15.33 plus storage);
  - Container Insights;
  - Performance Insights;
  - VPC flow logs;
  - customer-managed KMS keys.
- **CPU credits.** The `db-cpu-surplus-charged` alarm tells you when credit charges start. If they persist, find the load or move to db.t4g.small.
- **Cheaper options.**
  - `enable_waf = false` saves about $10 a month, at the cost of rate limiting and the managed rules.
  - SSM Parameter Store instead of Secrets Manager would save $1.20 a month.
- **Bigger options.** db.t4g.small costs $30.66 instead of $15.33. A second web task adds $9.47.

## Security scan

CI runs Checkov on the Terraform. The fixed findings are:

- descriptions on every security group rule;
- clean-up of incomplete uploads in the state bucket;
- RDS log export to CloudWatch;
- RDS IAM authentication enabled.
- AWS WAF attached to the load balancer.

The findings accepted on purpose are listed with reasons in `infra/terraform/.checkov.yaml`. They fall into two groups:

- **Cost.** Multi-AZ, enhanced monitoring, WAF request logs (metrics and sampled requests are on), flow logs, load balancer access logs, customer-managed KMS keys, 1-year log retention, state bucket replication.
- **Design.** Public addresses on tasks instead of a NAT gateway; an HTTP listener until a certificate is set; a disposable app layer without load-balancer deletion protection; an unencrypted alert topic (CloudWatch cannot publish to one encrypted with the AWS-managed key); a deploy role with broad rights to the services it deploys. That role is limited as described under Security choices and usable only from approved runs of this repository.

Recommended hardening when the budget allows:

1. Snapshot copies to a second region.
2. Multi-AZ for the database.
3. Rotating the database password: update the RDS master password and the `database-url` secret together, then force a new deployment.

Not adopted on purpose: AWS's anonymous-IP rule list. It would block VPN users and API clients on cloud hosts. Checkov's Log4j check (`CKV2_AWS_76`) asks for it, but the KnownBadInputs rules already block Log4j exploits.

## Manual configuration checklist

- [ ] Use an AWS account dedicated to this site.
- [ ] Run the bootstrap stack with `alert_email`, keep its local state file safe, and click the subscription confirmation email (step 1).
- [ ] Create the `production` environment with you as the only required reviewer and `main` as the only deployment rule, **then** set the variables (step 2). `bootstrap.sh` does and verifies this.
- [ ] Confirm `humanitylabs.in` is registered in the deployment account, with automatic renewal on (step 4). The certificate, DNS records and redirects are created by AWS provision.
- [ ] Before the first provision: verify `humanitylabs.in` in SES, get production access, and store the SMTP credentials (step 5). Deploys check all three.
- [ ] Make yourself a curator (step 3).
- [ ] Run **AWS restore** in `verify` mode once to prove the backups work.
