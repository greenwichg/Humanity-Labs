variable "aws_region" {
  description = "AWS region for everything. ap-south-1 is Mumbai."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "humanity-labs"
}

variable "github_repository" {
  description = "owner/name of the GitHub repository whose workflows may deploy."
  type        = string
  default     = "greenwichg/Humanity-Labs"
}

variable "github_environment" {
  description = "GitHub environment that gates deployments. Configure required reviewers on it."
  type        = string
  default     = "production"
}

variable "github_default_branch" {
  description = "Branch whose scheduled workflows may take backups."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Set to false if the account already has the token.actions.githubusercontent.com OIDC provider."
  type        = bool
  default     = true
}
