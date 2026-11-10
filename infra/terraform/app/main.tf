# ---------------------------------------------------------------- IAM

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Used by ECS itself to pull the image, write logs and read the three secrets. The app needs no AWS permissions.
resource "aws_iam_role" "execution" {
  name                 = "${var.project}-ecs-execution"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  permissions_boundary = local.platform.ecs_permissions_boundary_arn # required: the deploy role may only create bounded roles
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-app-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [local.platform.database_url_secret_arn, local.platform.app_secret_key_arn, local.platform.smtp_secret_arn]
    }]
  })
}

# ---------------------------------------------------------------- tasks

locals {
  image = "${local.platform.ecr_repository_url}:${var.image_tag}"
  environment = [
    { name = "BATCHLIGHT_ENV", value = "production" },
    { name = "BATCHLIGHT_BASE_URL", value = local.base_url },
    { name = "BATCHLIGHT_SECURE_COOKIES", value = tostring(local.https) },
    { name = "BATCHLIGHT_TRUST_PROXY_HEADERS", value = "true" },
    { name = "FORWARDED_ALLOW_IPS", value = "*" }, # only the load balancer can reach the tasks
    { name = "WEB_CONCURRENCY", value = tostring(var.web_concurrency) },
    { name = "BATCHLIGHT_EMAIL_BACKEND", value = "smtp" }, # production requires working email (see email-check)
    { name = "BATCHLIGHT_EMAIL_FROM", value = var.email_from },
    { name = "BATCHLIGHT_SMTP_HOST", value = var.smtp_host != "" ? var.smtp_host : "email-smtp.${var.aws_region}.amazonaws.com" },
    { name = "BATCHLIGHT_SMTP_PORT", value = tostring(var.smtp_port) },
    { name = "BATCHLIGHT_DB_POOL_SIZE", value = tostring(var.db_pool_size) },
    { name = "BATCHLIGHT_DB_MAX_OVERFLOW", value = tostring(var.db_max_overflow) },
  ]
  secrets = [
    { name = "BATCHLIGHT_DATABASE_URL", valueFrom = local.platform.database_url_secret_arn },
    { name = "BATCHLIGHT_SECRET_KEY", valueFrom = local.platform.app_secret_key_arn },
    # The SMTP user name is an access key ID, so it comes from the secret too and stays out of the public deploy logs.
    { name = "BATCHLIGHT_SMTP_USERNAME", valueFrom = "${local.platform.smtp_secret_arn}:username::" },
    { name = "BATCHLIGHT_SMTP_PASSWORD", valueFrom = "${local.platform.smtp_secret_arn}:password::" },
  ]
  logs = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = local.platform.log_group_name
      "awslogs-region"        = var.aws_region
      "awslogs-stream-prefix" = "app"
    }
  }
}

resource "aws_ecs_cluster" "main" {
  name = var.project
  setting {
    name  = "containerInsights"
    value = "disabled" # enable for detailed metrics; it adds CloudWatch cost
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([{
    name                   = "app"
    image                  = local.image
    command                = ["web"]
    essential              = true
    readonlyRootFilesystem = false
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment            = local.environment
    secrets                = local.secrets
    logConfiguration       = merge(local.logs, { options = merge(local.logs.options, { "awslogs-stream-prefix" = "web" }) })
  }])
  # Ordering only: ECS needs these to start a task, and the deploy's targeted apply (aws-deploy-core.yml) creates
  # just the cluster and the one-off task definition before running migrations, so they must come along with it.
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.execution_secrets]
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([{
    name             = "app"
    image            = local.image
    command          = ["worker"]
    essential        = true
    environment      = local.environment
    secrets          = local.secrets
    logConfiguration = merge(local.logs, { options = merge(local.logs.options, { "awslogs-stream-prefix" = "worker" }) })
  }])
  # Ordering only: ECS needs these to start a task, and the deploy's targeted apply (aws-deploy-core.yml) creates
  # just the cluster and the one-off task definition before running migrations, so they must come along with it.
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.execution_secrets]
}

# One-off commands: migrations, seeding, database checks. Run with `aws ecs run-task` and a command override.
resource "aws_ecs_task_definition" "cli" {
  family                   = "${var.project}-cli"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }
  container_definitions = jsonencode([{
    name             = "app"
    image            = local.image
    command          = ["migrate"]
    essential        = true
    environment      = local.environment
    secrets          = local.secrets
    logConfiguration = merge(local.logs, { options = merge(local.logs.options, { "awslogs-stream-prefix" = "cli" }) })
  }])
  # Ordering only: ECS needs these to start a task, and the deploy's targeted apply (aws-deploy-core.yml) creates
  # just the cluster and the one-off task definition before running migrations, so they must come along with it.
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.execution_secrets]
}

# ---------------------------------------------------------------- load balancer

resource "aws_security_group" "alb" {
  name        = "${var.project}-alb"
  description = "Public HTTP and HTTPS to the load balancer"
  vpc_id      = local.platform.vpc_id
  tags        = { Name = "${var.project}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere (redirected to HTTPS when a certificate is set)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count             = local.https ? 1 : 0
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the app tasks only"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = local.platform.app_security_group_id
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = local.platform.app_security_group_id
  description                  = "Web traffic from the load balancer only"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_lb" "main" {
  name                       = var.project
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = local.platform.public_subnet_ids
  drop_invalid_header_fields = true
  idle_timeout               = 60
}

resource "aws_lb_target_group" "web" {
  name                 = "${var.project}-web"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = local.platform.vpc_id
  deregistration_delay = 30
  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
  }
}

resource "aws_lb_listener" "http_forward" {
  count             = local.https ? 0 : 1
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# With the domain: HTTP goes straight to https://<domain> (one hop, also for www and the load balancer's own name).
resource "aws_lb_listener" "http_redirect" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      host        = local.domain
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# HTTPS serves only the canonical host. Anything else (www, the load balancer's own name) is redirected to
# https://<domain> with the same path and query.
resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = local.certificate_arn
  default_action {
    type = "redirect"
    redirect {
      host        = local.domain
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener_rule" "canonical_host" {
  count        = local.https ? 1 : 0
  listener_arn = aws_lb_listener.https[0].arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
  condition {
    host_header {
      values = [local.domain]
    }
  }
}

# ---------------------------------------------------------------- services

resource "aws_ecs_service" "web" {
  name                               = "web"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.web.arn
  desired_count                      = var.web_count
  launch_type                        = "FARGATE"
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  propagate_tags                     = "SERVICE"
  enable_execute_command             = false
  wait_for_steady_state              = true

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.platform.public_subnet_ids
    security_groups  = [local.platform.app_security_group_id]
    assign_public_ip = true # outbound only: the security group admits traffic from the load balancer alone
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "app"
    container_port   = 8000
  }

  # The target group must be attached to the load balancer (by a listener or rule) before the service starts.
  depends_on = [aws_lb_listener.http_forward, aws_lb_listener_rule.canonical_host, aws_vpc_security_group_ingress_rule.app_from_alb]
}

resource "aws_ecs_service" "worker" {
  name                               = "worker"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.worker.arn
  desired_count                      = 1
  deployment_minimum_healthy_percent = 0 # never two workers at once
  deployment_maximum_percent         = 100
  propagate_tags                     = "SERVICE"
  enable_execute_command             = false

  capacity_provider_strategy {
    capacity_provider = var.worker_use_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.platform.public_subnet_ids
    security_groups  = [local.platform.app_security_group_id]
    assign_public_ip = true
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
