# Offline plan checks (mocked AWS provider; no credentials needed). Run: terraform init -backend=false && terraform test
mock_provider "aws" {
  mock_data "aws_iam_policy_document" { defaults = { json = "{}" } }
}
override_data {
  target = data.terraform_remote_state.platform
  values = { outputs = {
    vpc_id                       = "vpc-1", public_subnet_ids = ["subnet-a", "subnet-b"], app_security_group_id = "sg-app",
    ecr_repository_url           = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/humanity-labs", log_group_name = "/ecs/humanity-labs",
    database_url_secret_arn      = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/database-url-a",
    app_secret_key_arn           = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/app-secret-key-b",
    smtp_password_secret_arn     = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-password-c",
    alerts_topic_arn             = "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts",
    ecs_permissions_boundary_arn = "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
  } }
}
variables {
  state_bucket = "bucket"
  image_tag    = "abc123"
}
run "plan" {
  command = plan
  assert {
    condition     = aws_iam_role.execution.permissions_boundary == "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
    error_message = "execution role must carry the boundary"
  }
  assert {
    condition     = startswith(aws_iam_role.execution.name, "humanity-labs-ecs-")
    error_message = "execution role name must match the deploy role's allowed prefix"
  }
  assert {
    condition     = aws_cloudwatch_metric_alarm.worker_not_running.treat_missing_data == "breaching" && aws_cloudwatch_metric_alarm.web_unhealthy.treat_missing_data == "breaching"
    error_message = "missing data must count as down"
  }
}

run "arm64_pool_settings_and_waf" {
  command = plan
  variables {
    domain_name = "example.org" # makes the container definitions known at plan time (no load balancer name needed)
  }
  assert {
    condition = alltrue([
      for td in [aws_ecs_task_definition.web, aws_ecs_task_definition.worker, aws_ecs_task_definition.cli] :
      td.runtime_platform[0].cpu_architecture == "ARM64"
    ])
    error_message = "all tasks must run on ARM64 by default"
  }
  assert {
    condition     = contains(jsondecode(aws_ecs_task_definition.web.container_definitions)[0].environment, { name = "BATCHLIGHT_DB_POOL_SIZE", value = "5" })
    error_message = "the web task must use the smaller connection pool"
  }
  assert {
    condition     = length(aws_wafv2_web_acl_association.main) == 1 && contains([for r in aws_wafv2_web_acl.main[0].rule : r.statement[0].rate_based_statement[0].limit if length(r.statement[0].rate_based_statement) > 0], 3000)
    error_message = "WAF must be attached with the default rate limit"
  }
}

run "waf_can_be_turned_off" {
  command = plan
  variables {
    enable_waf       = false
    cpu_architecture = "X86_64"
  }
  assert {
    condition     = length(aws_wafv2_web_acl.main) == 0 && length(aws_wafv2_web_acl_association.main) == 0
    error_message = "enable_waf = false must remove WAF"
  }
  assert {
    condition     = aws_ecs_task_definition.worker.runtime_platform[0].cpu_architecture == "X86_64"
    error_message = "cpu_architecture must switch every task"
  }
}
