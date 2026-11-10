# Secrets are read by ECS when a task starts and are never written to the task definition.
# The database URL is written once by Terraform. The restore workflow may later point it at a restored database,
# so Terraform does not overwrite later edits (ignore_changes). The SMTP password is set by hand after setup.

resource "random_password" "app_secret_key" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.project}/database-url"
  description             = "SQLAlchemy URL of the application database (TLS required)"
  recovery_window_in_days = 0 # regenerated on rebuild; a waiting period would block re-provisioning under the same name
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+psycopg://${aws_db_instance.main.username}:${random_password.db.result}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/${aws_db_instance.main.db_name}?sslmode=require"
  lifecycle { ignore_changes = [secret_string] }
}

resource "aws_secretsmanager_secret" "app_secret_key" {
  name                    = "${var.project}/app-secret-key"
  description             = "BATCHLIGHT_SECRET_KEY: hashes reporters' network addresses for rate limiting"
  recovery_window_in_days = 0 # regenerated on rebuild; a waiting period would block re-provisioning under the same name
}

resource "aws_secretsmanager_secret_version" "app_secret_key" {
  secret_id     = aws_secretsmanager_secret.app_secret_key.id
  secret_string = random_password.app_secret_key.result
}

resource "aws_secretsmanager_secret" "smtp_password" {
  name                    = "${var.project}/smtp-password"
  description             = "SMTP password (for example Amazon SES SMTP credentials). Set by hand; see docs/AWS_DEPLOYMENT.md."
  recovery_window_in_days = 0 # regenerated on rebuild; a waiting period would block re-provisioning under the same name
}

resource "aws_secretsmanager_secret_version" "smtp_password" {
  secret_id     = aws_secretsmanager_secret.smtp_password.id
  secret_string = "not-set"
  lifecycle { ignore_changes = [secret_string] }
}
