# Amazon RDS for PostgreSQL. Checked against the application: it needs PostgreSQL 12+ with the citext and pgcrypto
# extensions (both allowed on RDS and trusted, so the master user can create them), generated columns, GIN indexes
# and full-text search. The test suite and a migration run as a non-superuser confirm this on PostgreSQL 16.

locals {
  db_identifier = coalesce(var.db_identifier, "${var.project}-db")
}

resource "random_password" "db" {
  length  = 40
  special = false # keeps the connection URL free of characters that need escaping
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "main" {
  name_prefix = "${var.project}-pg${var.db_engine_version}-"
  family      = "postgres${var.db_engine_version}"
  description = "Humanity Labs PostgreSQL settings"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "2000"
  }
  lifecycle { create_before_destroy = true }
}

# RDS would create these itself with no expiry; creating them first keeps the retention bounded.
resource "aws_cloudwatch_log_group" "rds" {
  for_each          = toset(["postgresql", "upgrade"])
  name              = "/aws/rds/instance/${local.db_identifier}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_db_instance" "main" {
  identifier     = local.db_identifier
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  db_name  = "batchlight"
  username = "batchlight"
  password = random_password.db.result
  port     = 5432

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "20:00-20:30" # UTC, 01:30-02:00 IST
  maintenance_window        = "sun:21:00-sun:22:00"
  copy_tags_to_snapshot     = true
  delete_automated_backups  = false # keep automated backups for their retention period even if the database is deleted
  skip_final_snapshot       = false
  final_snapshot_identifier = var.final_snapshot_identifier
  deletion_protection       = var.deletion_protection

  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]
  iam_database_authentication_enabled = true

  snapshot_identifier         = var.restore_snapshot_identifier
  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = false
  apply_immediately           = false

  tags = { Name = "${var.project}-db", backup = "daily" }

  depends_on = [aws_cloudwatch_log_group.rds]

  lifecycle {
    # Restoring is a one-time choice made on creation; changing it later must not replace the database.
    ignore_changes = [snapshot_identifier]
  }
}
