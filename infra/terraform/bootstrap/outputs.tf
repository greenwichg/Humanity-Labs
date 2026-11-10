output "state_bucket" {
  description = "Set as the GitHub variable TF_STATE_BUCKET."
  value       = aws_s3_bucket.state.bucket
}

output "deploy_role_arn" {
  description = "Set as the GitHub variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}

output "backup_role_arn" {
  description = "Set as the GitHub variable AWS_BACKUP_ROLE_ARN."
  value       = aws_iam_role.backup.arn
}

output "alerts_topic_arn" {
  description = "SNS topic the CloudWatch alarms notify."
  value       = aws_sns_topic.alerts.arn
}

output "smtp_secret_name" {
  description = "Secrets Manager secret holding the SES SMTP credentials."
  value       = aws_secretsmanager_secret.smtp.name
}
