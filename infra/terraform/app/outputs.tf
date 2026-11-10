output "base_url" { value = local.base_url }
output "load_balancer_dns_name" { value = aws_lb.main.dns_name }
output "cluster_name" { value = aws_ecs_cluster.main.name }
output "cli_task_definition" { value = aws_ecs_task_definition.cli.arn }
output "subnet_ids" { value = local.platform.public_subnet_ids }
output "app_security_group_id" { value = local.platform.app_security_group_id }
