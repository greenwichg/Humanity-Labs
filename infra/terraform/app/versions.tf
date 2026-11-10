terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.80, < 7.0" }
  }
  backend "s3" {
    key          = "app/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags { tags = { project = var.project, environment = var.environment, managed-by = "terraform", stack = "app" } }
}

data "terraform_remote_state" "platform" {
  backend = "s3"
  config = {
    bucket = var.state_bucket
    key    = "platform/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  platform = data.terraform_remote_state.platform.outputs
  https    = var.certificate_arn != ""
  base_url = var.domain_name != "" ? "${local.https ? "https" : "http"}://${var.domain_name}" : "http://${aws_lb.main.dns_name}"
}
