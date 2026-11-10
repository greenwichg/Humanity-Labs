variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "project" {
  type    = string
  default = "humanity-labs"
}

variable "environment" {
  type    = string
  default = "production"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "db_instance_class" {
  description = "db.t4g.micro suits a small launch. Move to db.t4g.small if memory or connections run short."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL major version. The application's tests run on PostgreSQL 16."
  type        = string
  default     = "16"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "db_max_allocated_storage" {
  description = "Storage autoscaling ceiling in GB."
  type        = number
  default     = 100
}

variable "db_backup_retention_days" {
  description = "Automated backups and point-in-time recovery window (1-35 days). RDS runs these itself, independent of GitHub."
  type        = number
  default     = 35
}

variable "db_multi_az" {
  description = "A standby in a second zone roughly doubles the database cost."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Keep true. The deprovision workflow turns it off only for an approved full teardown."
  type        = bool
  default     = true
}

variable "db_identifier" {
  description = "RDS instance name. Leave empty for <project>-db; set it only when adopting a restored instance (docs/AWS_DEPLOYMENT.md)"
  type        = string
  default     = ""
}

variable "final_snapshot_identifier" {
  description = "Name of the snapshot RDS takes when the database is deleted. The deprovision workflow sets a dated name."
  type        = string
  default     = "humanity-labs-final"
}

variable "restore_snapshot_identifier" {
  description = "Create the database from this snapshot (for example a final snapshot after a teardown). Only read on creation."
  type        = string
  default     = null
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "image_retention_count" {
  description = "Tagged container images kept in ECR."
  type        = number
  default     = 30
}

variable "allow_destroy_images" {
  description = "Allow deleting the ECR repository while it still holds images."
  type        = bool
  default     = false
}
