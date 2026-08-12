# These outputs are what the Helm values need. Copy them across after apply:
#   terraform output -raw msk_bootstrap_brokers_tls

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "kubeconfig_command" {
  description = "Point kubectl at the cluster"
  value       = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.aws_region}"
}

output "msk_bootstrap_brokers_tls" {
  description = "Kafka bootstrap servers for helm values dependencies.kafka.bootstrapServers"
  value       = module.kafka.bootstrap_brokers_tls
}

output "clickhouse_endpoint" {
  description = "ClickHouse host for dependencies.clickhouse.host"
  value       = module.clickhouse.endpoint
}

output "postgres_endpoint" {
  description = "PostgreSQL host for dependencies.postgres.host"
  value       = aws_db_instance.postgres.address
}

output "redis_endpoint" {
  description = "Redis host for dependencies.redis.host"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "telemetry_bucket" {
  description = "S3 bucket holding raw telemetry archives"
  value       = aws_s3_bucket.telemetry.id
}

output "irsa_role_arn" {
  description = "Role for serviceAccount.annotations.eks.amazonaws.com/role-arn"
  value       = aws_iam_role.platform.arn
}

output "cluster_autoscaler_role_arn" {
  description = "Role for the cluster autoscaler's service account"
  value       = module.eks.cluster_autoscaler_role_arn
}

output "alert_topic_arn" {
  description = "SNS topic infrastructure alarms publish to"
  value       = module.monitoring.alert_topic_arn
}

output "vpc_id" {
  description = "VPC the platform runs in"
  value       = module.networking.vpc_id
}

output "nat_gateway_ips" {
  description = "Egress IPs, for allowlisting with upstream services"
  value       = module.networking.nat_gateway_ips
}

output "helm_values_snippet" {
  description = "Paste-ready values for the Helm chart"
  sensitive   = true
  value       = <<-YAML
    dependencies:
      kafka:
        bootstrapServers: ${module.kafka.bootstrap_brokers_tls}
      clickhouse:
        host: ${module.clickhouse.endpoint}
      postgres:
        host: ${aws_db_instance.postgres.address}
      redis:
        host: ${aws_elasticache_replication_group.redis.primary_endpoint_address}
    serviceAccount:
      annotations:
        eks.amazonaws.com/role-arn: ${aws_iam_role.platform.arn}
  YAML
}
