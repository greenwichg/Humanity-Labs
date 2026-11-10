# One-time setup, run by an administrator with their own AWS credentials (see docs/AWS_DEPLOYMENT.md):
#   - an encrypted, versioned S3 bucket for Terraform state (state locking uses S3 lock files);
#   - GitHub's OIDC identity provider, so workflows get short-lived credentials and no AWS keys are stored in GitHub;
#   - a deploy role usable only by jobs in the protected GitHub environment (which requires your approval);
#   - a backup role usable by scheduled runs on the default branch, limited to RDS snapshots it creates;
#   - the alert topic (email) used by the CloudWatch alarms, and a monthly cost budget.
# This stack keeps its own state locally (terraform.tfstate here). Keep that file safe or re-import if lost.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.80, < 7.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = { project = var.project, managed-by = "terraform", stack = "bootstrap" } }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = "${var.project}-tfstate-${local.account_id}-${var.aws_region}"
  oidc_host    = "token.actions.githubusercontent.com"
  name_prefix  = var.project
}

# ---------------------------------------------------------------- Terraform state bucket

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 90 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# ---------------------------------------------------------------- GitHub OIDC

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:oidc-provider/${local.oidc_host}"
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only jobs that run in the protected environment, which needs a reviewer's approval.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_environment}"]
    }
  }
}

data "aws_iam_policy_document" "backup_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Scheduled and manual runs of workflows on the default branch only.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_default_branch}"]
    }
  }
}

# ---------------------------------------------------------------- deploy role
#
# The deploy role runs Terraform for the platform and app stacks, so it manages networking, ECS, RDS and the load
# balancer. It is kept from becoming an account administrator, and from destroying or leaking backups:
#   - it can only create roles named <project>-ecs-* that carry the permissions boundary below, can attach only the
#     ECS task execution policy, and can pass roles only to ECS tasks, so it cannot grant itself or a task more power;
#   - it cannot touch its own role or the backup role;
#   - it can read only this project's secrets;
#   - it can never delete or share RDS snapshots, delete retained automated backups or export snapshots, and
#     cannot attach policies that would share secrets, images, logs or disk snapshots with another account.

# The hosted zone Route 53 created when the domain was registered in this account.
data "aws_route53_zone" "site" {
  count        = var.domain_name != "" ? 1 : 0
  name         = var.domain_name
  private_zone = false
}

locals {
  zone_arn         = var.domain_name != "" ? "arn:${data.aws_partition.current.partition}:route53:::hostedzone/${data.aws_route53_zone.site[0].zone_id}" : ""
  ecs_boundary_arn = "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:policy/${local.name_prefix}-ecs-boundary"
  ecs_role_arns    = "arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${local.name_prefix}-ecs-*"
}

# The most any role created by the deploy role can ever do: what ECS needs to start the app's tasks.
data "aws_iam_policy_document" "ecs_boundary" {
  statement {
    sid = "PullImagesAndWriteLogs"
    actions = [
      "ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
      "logs:CreateLogStream", "logs:PutLogEvents"
    ]
    resources = ["*"]
  }
  statement {
    sid       = "ReadThisProjectsSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${local.account_id}:secret:${local.name_prefix}/*"]
  }
}

resource "aws_iam_policy" "ecs_boundary" {
  name        = "${local.name_prefix}-ecs-boundary"
  description = "Permissions boundary for roles the GitHub deploy role creates"
  policy      = data.aws_iam_policy_document.ecs_boundary.json
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "TerraformState"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
  }
  statement {
    sid = "ManageServices"
    actions = [
      "ec2:*", "elasticloadbalancing:*", "ecs:*", "ecr:*", "rds:*", "logs:*",
      "cloudwatch:*", "application-autoscaling:*", "wafv2:*",
      "acm:RequestCertificate", "acm:DescribeCertificate", "acm:DeleteCertificate", "acm:ListCertificates",
      "acm:ListTagsForCertificate", "acm:AddTagsToCertificate", "acm:RemoveTagsFromCertificate",
      "kms:DescribeKey", "kms:ListAliases", "sts:GetCallerIdentity", "servicediscovery:*"
    ]
    resources = ["*"]
  }
  # Read-only SES checks the deploy runs before rolling out: production access and the verified sending domain.
  statement {
    sid       = "CheckEmailSetup"
    actions   = ["ses:GetAccount", "ses:GetEmailIdentity"]
    resources = ["*"]
  }
  statement {
    sid       = "ThisProjectsSecrets"
    actions   = ["secretsmanager:*"]
    resources = ["arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${local.account_id}:secret:${local.name_prefix}/*"]
  }
  # DNS: only the site's hosted zone, and only the records Terraform manages there (the domain, www and the
  # certificate validation records), so the role cannot change mail or any other record of the domain.
  statement {
    sid       = "FindHostedZones"
    actions   = ["route53:ListHostedZones", "route53:ListHostedZonesByName", "route53:GetChange"]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = local.zone_arn == "" ? [] : [local.zone_arn]
    content {
      sid       = "ReadSiteZone"
      actions   = ["route53:GetHostedZone", "route53:ListResourceRecordSets", "route53:ListTagsForResource"]
      resources = [statement.value]
    }
  }
  dynamic "statement" {
    for_each = local.zone_arn == "" ? [] : [local.zone_arn]
    content {
      sid       = "ChangeOnlySiteRecords"
      actions   = ["route53:ChangeResourceRecordSets"]
      resources = [statement.value]
      condition {
        test     = "ForAllValues:StringLike"
        variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
        values   = [var.domain_name, "www.${var.domain_name}", "_*.${var.domain_name}"]
      }
    }
  }
  statement {
    sid       = "CreateBoundedEcsRoles"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = [local.ecs_role_arns]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.ecs_boundary_arn]
    }
  }
  statement {
    sid = "ManageEcsRoles"
    actions = [
      "iam:GetRole", "iam:DeleteRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy"
    ]
    resources = [local.ecs_role_arns]
  }
  statement {
    sid       = "AttachOnlyTheEcsExecutionPolicy"
    actions   = ["iam:AttachRolePolicy", "iam:DetachRolePolicy"]
    resources = [local.ecs_role_arns]
    condition {
      test     = "ArnEquals"
      variable = "iam:PolicyARN"
      values   = ["arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"]
    }
  }
  statement {
    sid       = "PassRolesToEcsTasksOnly"
    actions   = ["iam:PassRole"]
    resources = [local.ecs_role_arns]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "iam:AWSServiceName"
      values   = ["ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com", "rds.amazonaws.com"]
    }
  }
  # Explicit denies win over any allow, including one the role might gain later.
  statement {
    sid    = "NeverDestroyOrShareBackups"
    effect = "Deny"
    actions = [
      "rds:DeleteDBSnapshot", "rds:DeleteDBClusterSnapshot", "rds:ModifyDBSnapshotAttribute",
      "rds:ModifyDBClusterSnapshotAttribute", "rds:DeleteDBInstanceAutomatedBackup", "rds:StartExportTask"
    ]
    resources = ["*"]
  }
  statement {
    sid    = "NeverShareOutsideTheAccount"
    effect = "Deny"
    actions = [
      "secretsmanager:PutResourcePolicy", "ecr:SetRepositoryPolicy", "logs:PutSubscriptionFilter", "logs:PutDestination",
      "logs:PutDestinationPolicy", "logs:CreateExportTask", "ec2:ModifySnapshotAttribute", "ec2:ModifyImageAttribute"
    ]
    resources = ["*"]
  }
  statement {
    sid       = "NeverRemoveTheBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "deploy" {
  name                 = "${local.name_prefix}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 7200
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# ---------------------------------------------------------------- backup role

data "aws_iam_policy_document" "backup" {
  statement {
    sid       = "Read"
    actions   = ["rds:DescribeDBInstances", "rds:DescribeDBSnapshots", "rds:ListTagsForResource"]
    resources = ["*"]
  }
  statement {
    sid       = "CreateTaggedSnapshots"
    actions   = ["rds:CreateDBSnapshot", "rds:AddTagsToResource"]
    resources = ["arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${local.account_id}:db:${local.name_prefix}-*", "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${local.account_id}:snapshot:${local.name_prefix}-daily-*"]
  }
  # Pruning may delete only the snapshots this workflow created.
  statement {
    sid       = "DeleteOwnSnapshots"
    actions   = ["rds:DeleteDBSnapshot"]
    resources = ["arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${local.account_id}:snapshot:${local.name_prefix}-daily-*"]
    condition {
      test     = "StringEquals"
      variable = "rds:snapshot-tag/created-by"
      values   = ["github-backup"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${local.name_prefix}-github-backup"
  assume_role_policy = data.aws_iam_policy_document.backup_trust.json
}

resource "aws_iam_role_policy" "backup" {
  name   = "backup"
  role   = aws_iam_role.backup.id
  policy = data.aws_iam_policy_document.backup.json
}

# ---------------------------------------------------------------- SMTP credentials

# Amazon SES SMTP credentials as JSON {"username": "...", "password": "..."}. Only the empty secret is created here;
# bootstrap.sh asks for the values and stores them with your credentials, so they never enter Terraform state or the
# public deploy logs. It lives in this stack so the credentials exist before the first deploy and survive teardowns.
resource "aws_secretsmanager_secret" "smtp" {
  name        = "${local.name_prefix}/smtp"
  description = "Amazon SES SMTP credentials as JSON {username, password}; set with infra/scripts/bootstrap.sh"
}

# ---------------------------------------------------------------- alerts and budget

# CloudWatch alarms in the platform and app stacks send to this topic. The topic is not encrypted on purpose:
# CloudWatch alarms cannot publish to a topic encrypted with the AWS-managed SNS key.
resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS sends a confirmation email; alerts arrive only after you confirm it
}

resource "aws_budgets_budget" "monthly" {
  count        = var.alert_email != "" ? 1 : 0
  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
