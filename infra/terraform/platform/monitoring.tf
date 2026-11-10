# Database alarms. They notify the <project>-alerts SNS topic created by the bootstrap stack (subscribe your email
# there with -var alert_email=...). Nine alarms across both stacks stay inside CloudWatch's free tier of ten.

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

# Failed email deliveries (password resets, invitations, alert digests). The app logs them as
# "<time> WARNING batchlight.email EMAIL_DELIVERY_FAILED to=p***@example.org error=..." (address masked). The whole
# phrase is matched, not just the word, because the web server's access log records request paths: a visitor could
# put the word in a URL, but not the spaces (uvicorn percent-encodes them).
resource "aws_cloudwatch_log_metric_filter" "email_delivery_failed" {
  name           = "${var.project}-email-delivery-failed"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"WARNING batchlight.email EMAIL_DELIVERY_FAILED\""
  metric_transformation {
    name      = "EmailDeliveryFailed"
    namespace = "HumanityLabs"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "email_delivery_failed" {
  alarm_name          = "${var.project}-email-delivery-failed"
  alarm_description   = "Emails (password resets, invitations or alerts) failed to send in the last 5 minutes. Check SES and the SMTP credentials; the audit log has password_reset_email_failed events."
  namespace           = "HumanityLabs"
  metric_name         = aws_cloudwatch_log_metric_filter.email_delivery_failed.metric_transformation[0].name
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [local.alerts_topic_arn]
  ok_actions          = [local.alerts_topic_arn]
}
