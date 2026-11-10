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
