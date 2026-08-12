output "cluster_name" {
  description = "GKE cluster name"
  value       = module.gke.cluster_name
}

output "kubeconfig_command" {
  description = "Point kubectl at the cluster"
  value       = module.gke.kubeconfig_command
}

output "pubsub_topics" {
  description = "Pub/Sub topics backing the event stream"
  value       = [for topic in google_pubsub_topic.topics : topic.name]
}

output "postgres_private_ip" {
  description = "Cloud SQL private IP for dependencies.postgres.host"
  value       = google_sql_database_instance.postgres.private_ip_address
}

output "redis_host" {
  description = "Memorystore host for dependencies.redis.host"
  value       = google_redis_instance.this.host
}

output "redis_port" {
  description = "Memorystore port"
  value       = google_redis_instance.this.port
}

output "artifact_registry" {
  description = "Registry for image.registry"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/search-metrics"
}

output "workload_service_account" {
  description = "Email for serviceAccount.annotations.iam.gke.io/gcp-service-account"
  value       = module.gke.workload_service_account_email
}

output "telemetry_bucket" {
  description = "Bucket holding telemetry archives"
  value       = google_storage_bucket.telemetry.name
}

output "helm_values_snippet" {
  description = "Paste-ready values for the Helm chart"
  sensitive   = true
  value       = <<-YAML
    image:
      registry: ${var.region}-docker.pkg.dev/${var.project_id}/search-metrics
    dependencies:
      postgres:
        host: ${google_sql_database_instance.postgres.private_ip_address}
      redis:
        host: ${google_redis_instance.this.host}
        port: ${google_redis_instance.this.port}
        tls: true
    serviceAccount:
      annotations:
        iam.gke.io/gcp-service-account: ${module.gke.workload_service_account_email}
  YAML
}
