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
  # The domain and its certificate come from the platform stack (platform/domain.tf).
  domain          = try(local.platform.domain_name, "")
  certificate_arn = try(local.platform.certificate_arn, "")
  zone_id         = try(local.platform.hosted_zone_id, "")
  https           = local.certificate_arn != ""
  base_url        = local.https ? "https://${local.domain}" : "http://${aws_lb.main.dns_name}"
}
