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

variable "state_bucket" {
  description = "Terraform state bucket (bootstrap output), used to read the platform stack's outputs."
  type        = string
}

variable "image_tag" {
  description = "Container image tag in ECR (the deploy workflow uses the git commit SHA)."
  type        = string
}

variable "domain_name" {
  description = "Public host name, for example works.example.org. Empty uses the load balancer's own name over HTTP."
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate for domain_name in the same region. Empty serves plain HTTP (not for real users)."
  type        = string
  default     = ""
}

variable "web_cpu" {
  description = "0.25 vCPU is enough for a small launch; raise to 512 or 1024 as traffic grows."
  type        = number
  default     = 256
}

variable "web_memory" {
  type    = number
  default = 512
}

variable "web_count" {
  type    = number
  default = 1
}

variable "web_concurrency" {
  description = "Uvicorn worker processes per web task."
  type        = number
  default     = 2
}

variable "worker_cpu" {
  type    = number
  default = 256
}

variable "worker_memory" {
  description = "The nationwide PMGSY refresh peaks at about 1.1 GB, so the worker gets 2 GB."
  type        = number
  default     = 2048
}

variable "worker_use_spot" {
  description = "Run the ingestion worker on Fargate Spot (about 70% cheaper; it may be interrupted and restarts)."
  type        = bool
  default     = true
}

variable "email_backend" {
  description = "console (logs only), smtp or disabled. Switch to smtp after setting the SMTP secret."
  type        = string
  default     = "console"
}

variable "email_from" {
  type    = string
  default = "Humanity Labs <alerts@example.org>"
}

variable "smtp_host" {
  type    = string
  default = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}

variable "smtp_username" {
  type    = string
  default = ""
}
