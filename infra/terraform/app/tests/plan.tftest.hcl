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
