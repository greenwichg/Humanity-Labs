terraform {
  required_version = ">= 1.10"
  required_providers {
    aws    = { source = "hashicorp/aws", version = ">= 5.80, < 7.0" }
    random = { source = "hashicorp/random", version = ">= 3.6, < 4.0" }
  }
  # Bucket and region are passed with -backend-config (see docs/AWS_DEPLOYMENT.md). S3 lock files need Terraform 1.10+.
  backend "s3" {
    key          = "platform/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = { project = var.project, environment = var.environment, managed-by = "terraform", stack = "platform" } }
}
