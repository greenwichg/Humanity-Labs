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
