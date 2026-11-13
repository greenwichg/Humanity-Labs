# Offline plan checks (mocked AWS provider; no credentials needed). Run: terraform init -backend=false && terraform test
mock_provider "aws" {
  mock_data "aws_availability_zones" { defaults = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] } }
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
  mock_data "aws_route53_zone" { defaults = { zone_id = "Z0HUMANITYLABS", name = "humanitylabs.in" } }
  mock_data "aws_secretsmanager_secret" { defaults = { arn = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf" } }
}
mock_provider "random" {}
run "plan" {
  command = plan
  assert {
    condition     = aws_db_instance.main.backup_retention_period == 35 && aws_db_instance.main.deletion_protection && !aws_db_instance.main.skip_final_snapshot
    error_message = "database protection settings"
  }
  assert {
    condition     = length([for a in [aws_cloudwatch_metric_alarm.db_storage_low, aws_cloudwatch_metric_alarm.db_cpu_credits_charged, aws_cloudwatch_metric_alarm.db_cpu_high, aws_cloudwatch_metric_alarm.db_connections_high] : a if contains(a.alarm_actions, "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts")]) == 4
    error_message = "alarms must notify the alert topic"
  }
  assert {
    condition     = output.ecs_permissions_boundary_arn == "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
    error_message = "boundary ARN must match the bootstrap policy name"
  }
}

run "certificate_for_the_domain_and_www" {
  command = plan
  assert {
    condition     = aws_acm_certificate.site[0].domain_name == "humanitylabs.in" && aws_acm_certificate.site[0].subject_alternative_names == toset(["www.humanitylabs.in"]) && aws_acm_certificate.site[0].validation_method == "DNS"
    error_message = "one DNS-validated certificate must cover humanitylabs.in and www"
  }
  assert {
    condition     = keys(aws_route53_record.certificate_validation) == ["humanitylabs.in", "www.humanitylabs.in"] && alltrue([for r in aws_route53_record.certificate_validation : r.zone_id == "Z0HUMANITYLABS"])
    error_message = "validation records must go into the domain's hosted zone"
  }
}

run "no_domain_means_no_certificate" {
  command = plan
  variables {
    domain_name = ""
  }
  assert {
    condition     = length(aws_acm_certificate.site) == 0 && length(aws_route53_record.certificate_validation) == 0 && output.certificate_arn == ""
    error_message = "an empty domain must not request a certificate"
  }
}

run "smtp_credentials_come_from_the_bootstrap_secret" {
  command = plan
  assert {
    condition     = data.aws_secretsmanager_secret.smtp.name == "humanity-labs/smtp" && output.smtp_secret_arn == "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf"
    error_message = "the platform must read the SMTP secret created by the bootstrap stack, not create its own"
  }
}

run "failed_email_deliveries_raise_an_alarm" {
  command = plan
  assert {
    condition     = aws_cloudwatch_log_metric_filter.email_delivery_failed.pattern == "\"WARNING batchlight.email EMAIL_DELIVERY_FAILED\"" && aws_cloudwatch_log_metric_filter.email_delivery_failed.log_group_name == "/ecs/humanity-labs"
    error_message = "the metric filter must count the app's EMAIL_DELIVERY_FAILED log lines"
  }
  assert {
    condition     = contains(aws_cloudwatch_metric_alarm.email_delivery_failed.alarm_actions, "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts") && aws_cloudwatch_metric_alarm.email_delivery_failed.threshold == 0
    error_message = "any failed delivery must notify the alert topic"
  }
}

run "no_photo_bucket_until_approved" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket.media) == 0 && output.media_bucket_name == ""
    error_message = "the Community photo bucket must not exist unless community_media is set"
  }
}

run "photo_bucket_is_private_encrypted_and_https_only" {
  command = plan
  variables {
    community_media = true
  }
  assert {
    condition     = aws_s3_bucket.media[0].bucket == "humanity-labs-media-111122223333"
    error_message = "bucket name must match the boundary and deploy-role patterns in the bootstrap stack"
  }
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.media[0].block_public_acls, aws_s3_bucket_public_access_block.media[0].block_public_policy,
      aws_s3_bucket_public_access_block.media[0].ignore_public_acls, aws_s3_bucket_public_access_block.media[0].restrict_public_buckets
    ]) && aws_s3_bucket_ownership_controls.media[0].rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "photos must never be publicly readable from S3"
  }
  assert {
    condition     = one([for r in aws_s3_bucket_server_side_encryption_configuration.media[0].rule : one(r.apply_server_side_encryption_by_default).sse_algorithm]) == "AES256"
    error_message = "photos must be encrypted at rest"
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.media[0].policy).Statement[0].Effect == "Deny" && jsondecode(aws_s3_bucket_policy.media[0].policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false" && jsondecode(aws_s3_bucket_policy.media[0].policy).Statement[0].Resource == ["arn:aws:s3:::humanity-labs-media-111122223333", "arn:aws:s3:::humanity-labs-media-111122223333/*"]
    error_message = "the bucket must refuse plain HTTP"
  }
  assert {
    condition     = one([for r in aws_s3_bucket_lifecycle_configuration.media[0].rule : one(r.noncurrent_version_expiration).noncurrent_days]) == 7
    error_message = "unblurred originals must not be kept as old versions for long"
  }
}
