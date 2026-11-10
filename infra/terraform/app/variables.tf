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


variable "email_from" {
  type    = string
  default = "Humanity Labs <alerts@humanitylabs.in>"
}

variable "smtp_host" {
  description = "SMTP server. Empty uses Amazon SES in this region (email-smtp.<region>.amazonaws.com)."
  type        = string
  default     = ""
}

variable "smtp_port" {
  type    = number
  default = 587
}


variable "cpu_architecture" {
  description = "ARM64 (Graviton, about 44% cheaper per vCPU-hour in Mumbai) or X86_64. Must match the image the deploy workflow builds (repository variable CPU_ARCHITECTURE)."
  type        = string
  default     = "ARM64"
  validation {
    condition     = contains(["ARM64", "X86_64"], var.cpu_architecture)
    error_message = "cpu_architecture must be ARM64 or X86_64."
  }
}

variable "db_pool_size" {
  description = "Database connections kept open per process. With the defaults, at most (2 web processes x 2 tasks during a deploy + worker + one one-off task) x 10 = 60 connections."
  type        = number
  default     = 5
}

variable "db_max_overflow" {
  description = "Extra database connections a process may open under load, on top of db_pool_size."
  type        = number
  default     = 5
}

variable "enable_waf" {
  description = "Put AWS WAF in front of the load balancer: a per-address rate limit plus AWS managed rules (about $9 a month plus $0.60 per million requests)."
  type        = bool
  default     = true
}

variable "waf_rate_limit" {
  description = "Requests per 5 minutes from one IP address before WAF blocks it. Kept generous because many Indian mobile users share addresses (carrier-grade NAT)."
  type        = number
  default     = 3000
}
