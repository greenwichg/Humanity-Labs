# Database alarms. They notify the <project>-alerts SNS topic created by the bootstrap stack (subscribe your email
# there with -var alert_email=...). Eight alarms across both stacks stay inside CloudWatch's free tier of ten.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  alerts_topic_arn = "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.project}-alerts"
  db_dimensions    = { DBInstanceIdentifier = aws_db_instance.main.identifier }
}

resource "aws_cloudwatch_metric_alarm" "db_storage_low" {
  alarm_name          = "${var.project}-db-storage-low"
  alarm_description   = "Database free storage below 2 GiB. Storage autoscaling should grow it; check that it can (max ${var.db_max_allocated_storage} GiB)."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = local.db_dimensions
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 2 * 1024 * 1024 * 1024
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu_credits_charged" {
  alarm_name          = "${var.project}-db-cpu-surplus-charged"
  alarm_description   = "The burstable database is spending surplus CPU credits, which are billed. Find the load, or move to a larger class."
  namespace           = "AWS/RDS"
  metric_name         = "CPUSurplusCreditsCharged"
  dimensions          = local.db_dimensions
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu_high" {
  alarm_name          = "${var.project}-db-cpu-high"
  alarm_description   = "Database CPU above 80% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = local.db_dimensions
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  comparison_operator = "GreaterThanThreshold"
  threshold           = 80
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "db_connections_high" {
  alarm_name          = "${var.project}-db-connections-high"
  alarm_description   = "More than 70 database connections; a db.t4g.micro allows roughly 80-110."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = local.db_dimensions
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = 70
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}
