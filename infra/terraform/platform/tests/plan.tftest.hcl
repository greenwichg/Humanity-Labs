# Offline plan checks (mocked AWS provider; no credentials needed). Run: terraform init -backend=false && terraform test
mock_provider "aws" {
  mock_data "aws_availability_zones" { defaults = { names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"] } }
  mock_data "aws_caller_identity" { defaults = { account_id = "111122223333" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
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
