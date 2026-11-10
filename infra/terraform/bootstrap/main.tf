# One-time setup, run by an administrator with their own AWS credentials (see docs/AWS_DEPLOYMENT.md):
#   - an encrypted, versioned S3 bucket for Terraform state (state locking uses S3 lock files);
#   - GitHub's OIDC identity provider, so workflows get short-lived credentials and no AWS keys are stored in GitHub;
#   - a deploy role usable only by jobs in the protected GitHub environment (which requires your approval);
#   - a backup role usable by scheduled runs on the default branch, limited to RDS snapshots it creates.
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

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "TerraformState"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
  }
  statement {
    sid = "ManageServices"
    actions = [
      "ec2:*", "elasticloadbalancing:*", "ecs:*", "ecr:*", "rds:*", "logs:*", "secretsmanager:*",
      "cloudwatch:*", "application-autoscaling:*", "acm:DescribeCertificate", "acm:ListCertificates",
      "kms:DescribeKey", "kms:ListAliases", "sts:GetCallerIdentity", "servicediscovery:*"
    ]
    resources = ["*"]
  }
  statement {
    sid = "ManageProjectRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole",
      "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:PutRolePolicy", "iam:GetRolePolicy",
      "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListInstanceProfilesForRole",
      "iam:UpdateAssumeRolePolicy", "iam:PassRole"
    ]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${local.account_id}:role/${local.name_prefix}-*"]
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
