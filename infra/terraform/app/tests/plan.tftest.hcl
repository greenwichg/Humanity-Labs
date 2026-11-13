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
    smtp_secret_arn              = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf",
    alerts_topic_arn             = "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts",
    ecs_permissions_boundary_arn = "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
    domain_name                  = "humanitylabs.in"
    certificate_arn              = "arn:aws:acm:ap-south-1:111122223333:certificate/abc"
    hosted_zone_id               = "Z0HUMANITYLABS"
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

run "https_on_the_domain_with_www_redirect" {
  command = plan
  assert {
    condition     = output.base_url == "https://humanitylabs.in"
    error_message = "the site address must be https://humanitylabs.in"
  }
  assert {
    condition     = contains(jsondecode(aws_ecs_task_definition.web.container_definitions)[0].environment, { name = "BATCHLIGHT_BASE_URL", value = "https://humanitylabs.in" }) && contains(jsondecode(aws_ecs_task_definition.web.container_definitions)[0].environment, { name = "BATCHLIGHT_SECURE_COOKIES", value = "true" })
    error_message = "the app must use the https address and Secure cookies"
  }
  assert {
    condition     = aws_lb_listener.https[0].certificate_arn == "arn:aws:acm:ap-south-1:111122223333:certificate/abc" && aws_lb_listener.https[0].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    error_message = "HTTPS must use the platform certificate and the TLS 1.2/1.3 policy"
  }
  assert {
    condition     = aws_lb_listener.https[0].default_action[0].type == "redirect" && aws_lb_listener.https[0].default_action[0].redirect[0].host == "humanitylabs.in" && aws_lb_listener.https[0].default_action[0].redirect[0].status_code == "HTTP_301"
    error_message = "any other host (www, the load balancer's name) must get a 301 to humanitylabs.in"
  }
  assert {
    condition     = one(aws_lb_listener_rule.canonical_host[0].condition[*].host_header[0].values) == toset(["humanitylabs.in"]) && aws_lb_listener_rule.canonical_host[0].action[0].type == "forward"
    error_message = "only humanitylabs.in is forwarded to the app"
  }
  assert {
    condition     = aws_lb_listener.http_redirect[0].default_action[0].redirect[0].host == "humanitylabs.in" && aws_lb_listener.http_redirect[0].default_action[0].redirect[0].protocol == "HTTPS" && length(aws_lb_listener.http_forward) == 0
    error_message = "HTTP must redirect straight to https://humanitylabs.in"
  }
  assert {
    condition     = aws_route53_record.apex[0].name == "humanitylabs.in" && aws_route53_record.www[0].name == "www.humanitylabs.in" && aws_route53_record.apex[0].zone_id == "Z0HUMANITYLABS" && aws_route53_record.www[0].type == "A"
    error_message = "the domain and www must point at the load balancer"
  }
}

run "without_a_domain_serves_http_on_the_load_balancer" {
  command = plan
  override_data {
    target = data.terraform_remote_state.platform
    values = { outputs = {
      vpc_id                       = "vpc-1", public_subnet_ids = ["subnet-a", "subnet-b"], app_security_group_id = "sg-app",
      ecr_repository_url           = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/humanity-labs", log_group_name = "/ecs/humanity-labs",
      database_url_secret_arn      = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/database-url-a",
      app_secret_key_arn           = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/app-secret-key-b",
      smtp_secret_arn              = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf",
      alerts_topic_arn             = "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts",
      ecs_permissions_boundary_arn = "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
      domain_name                  = ""
      certificate_arn              = ""
      hosted_zone_id               = ""
    } }
  }
  assert {
    condition     = length(aws_lb_listener.http_forward) == 1 && length(aws_lb_listener.https) == 0 && length(aws_route53_record.apex) == 0 && length(aws_route53_record.www) == 0
    error_message = "without a domain there is no HTTPS listener and no DNS record"
  }
}

run "production_email_uses_ses_smtp_with_secret_credentials" {
  command = plan
  assert {
    condition = alltrue([
      for e in [
        { name = "BATCHLIGHT_EMAIL_BACKEND", value = "smtp" },
        { name = "BATCHLIGHT_SMTP_HOST", value = "email-smtp.ap-south-1.amazonaws.com" },
        { name = "BATCHLIGHT_EMAIL_FROM", value = "Humanity Labs <alerts@humanitylabs.in>" },
      ] : contains(jsondecode(aws_ecs_task_definition.web.container_definitions)[0].environment, e)
    ])
    error_message = "production must send through SES SMTP from alerts@humanitylabs.in"
  }
  assert {
    condition = alltrue([
      for s in [
        { name = "BATCHLIGHT_SMTP_USERNAME", valueFrom = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf:username::" },
        { name = "BATCHLIGHT_SMTP_PASSWORD", valueFrom = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf:password::" },
      ] : contains(jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].secrets, s)
    ])
    error_message = "SMTP user name and password must come from the secret"
  }
  assert {
    condition     = !anytrue([for e in jsondecode(aws_ecs_task_definition.cli.container_definitions)[0].environment : e.name == "BATCHLIGHT_SMTP_USERNAME"])
    error_message = "the SMTP user name must not be a plain environment variable (it would show in public deploy logs)"
  }
}

# The deploy workflow (aws-deploy-core.yml, "Prepare app stack") applies only these two targets, then starts the email
# check and the migrations as one-off tasks. ECS can start those only if the execution role already has its
# permissions to pull the image, read the secrets and write logs, so the targeted plan must include them.
# tests/unit/test_deploy_tooling.py keeps these targets identical to the workflow's.
run "one_off_tasks_can_start_after_the_targeted_apply" {
  command = plan
  plan_options {
    target = [aws_ecs_cluster.main, aws_ecs_task_definition.cli]
  }
  assert {
    condition     = aws_iam_role_policy_attachment.execution.policy_arn == "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
    error_message = "the targeted apply must attach the ECS task execution policy (image pull and logs)"
  }
  assert {
    condition     = aws_iam_role_policy.execution_secrets.name == "read-app-secrets"
    error_message = "the targeted apply must give the execution role access to the app's secrets"
  }
}

run "no_task_role_without_the_photo_bucket" {
  command = plan
  assert {
    condition     = length(aws_iam_role.task) == 0 && aws_ecs_task_definition.web.task_role_arn == null && aws_ecs_task_definition.worker.task_role_arn == null
    error_message = "the app must have no AWS permissions while the photo bucket does not exist"
  }
}

run "task_role_reaches_only_photos_in_the_bucket" {
  command = plan
  override_data {
    target = data.terraform_remote_state.platform
    values = { outputs = {
      vpc_id                       = "vpc-1", public_subnet_ids = ["subnet-a", "subnet-b"], app_security_group_id = "sg-app",
      ecr_repository_url           = "111122223333.dkr.ecr.ap-south-1.amazonaws.com/humanity-labs", log_group_name = "/ecs/humanity-labs",
      database_url_secret_arn      = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/database-url-a",
      app_secret_key_arn           = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/app-secret-key-b",
      smtp_secret_arn              = "arn:aws:secretsmanager:ap-south-1:111122223333:secret:humanity-labs/smtp-AbCdEf",
      alerts_topic_arn             = "arn:aws:sns:ap-south-1:111122223333:humanity-labs-alerts",
      ecs_permissions_boundary_arn = "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
      domain_name                  = "humanitylabs.in"
      certificate_arn              = "arn:aws:acm:ap-south-1:111122223333:certificate/abc"
      hosted_zone_id               = "Z0HUMANITYLABS"
      media_bucket_name            = "humanity-labs-media-111122223333"
    } }
  }
  assert {
    condition     = aws_iam_role.task[0].name == "humanity-labs-ecs-task" && aws_iam_role.task[0].permissions_boundary == "arn:aws:iam::111122223333:policy/humanity-labs-ecs-boundary"
    error_message = "the task role must match the deploy role's allowed prefix and carry the boundary"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.task_media[0].policy).Statement[0].Resource == ["arn:aws:s3:::humanity-labs-media-111122223333/posts/*"] && toset(jsondecode(aws_iam_role_policy.task_media[0].policy).Statement[0].Action) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"])
    error_message = "the app may only read, write and delete photos under posts/"
  }
  assert {
    condition     = contains([for e in jsondecode(aws_ecs_task_definition.worker.container_definitions)[0].environment : e.name], "BATCHLIGHT_MEDIA_S3_BUCKET") && !contains([for e in jsondecode(aws_ecs_task_definition.web.container_definitions)[0].environment : e.name], "BATCHLIGHT_COMMUNITY_UPLOADS")
    error_message = "the bucket is passed to the app, but uploads are not switched on by Terraform"
  }
}
