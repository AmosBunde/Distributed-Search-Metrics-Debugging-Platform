output "resource_group" {
  description = "Resource group holding the platform"
  value       = azurerm_resource_group.this.name
}

output "cluster_name" {
  description = "AKS cluster name"
  value       = module.aks.cluster_name
}

output "kubeconfig_command" {
  description = "Point kubectl at the cluster"
  value       = module.aks.kubeconfig_command
}

output "eventhub_bootstrap_servers" {
  description = "Kafka-compatible endpoint for dependencies.kafka.bootstrapServers"
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net:9093"
}

output "eventhub_connection_string" {
  description = "SASL connection string for the Event Hubs namespace"
  value       = azurerm_eventhub_namespace_authorization_rule.platform.primary_connection_string
  sensitive   = true
}

output "postgres_endpoint" {
  description = "PostgreSQL host for dependencies.postgres.host"
  value       = azurerm_postgresql_flexible_server.this.fqdn
}

output "redis_endpoint" {
  description = "Redis host for dependencies.redis.host"
  value       = azurerm_redis_cache.this.hostname
}

output "redis_port" {
  description = "Redis TLS port — the non-TLS port is disabled"
  value       = azurerm_redis_cache.this.ssl_port
}

output "registry_login_server" {
  description = "Container registry for image.registry"
  value       = azurerm_container_registry.this.login_server
}

output "workload_identity_client_id" {
  description = "Client ID for serviceAccount.annotations.azure.workload.identity/client-id"
  value       = module.aks.workload_identity_client_id
}

output "storage_account" {
  description = "Storage account holding telemetry archives"
  value       = azurerm_storage_account.telemetry.name
}

output "helm_values_snippet" {
  description = "Paste-ready values for the Helm chart"
  sensitive   = true
  value       = <<-YAML
    image:
      registry: ${azurerm_container_registry.this.login_server}/search-metrics
    dependencies:
      kafka:
        bootstrapServers: ${azurerm_eventhub_namespace.this.name}.servicebus.windows.net:9093
      postgres:
        host: ${azurerm_postgresql_flexible_server.this.fqdn}
      redis:
        host: ${azurerm_redis_cache.this.hostname}
        port: ${azurerm_redis_cache.this.ssl_port}
        tls: true
    serviceAccount:
      annotations:
        azure.workload.identity/client-id: ${module.aks.workload_identity_client_id}
  YAML
}
