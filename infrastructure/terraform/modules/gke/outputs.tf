output "cluster_name" {
  description = "Name of the GKE cluster"
  value       = google_container_cluster.this.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = google_container_cluster.this.endpoint
  sensitive   = true
}

output "workload_service_account_email" {
  description = "Email for serviceAccount.annotations.iam.gke.io/gcp-service-account"
  value       = google_service_account.workload.email
}

output "workload_pool" {
  description = "Workload identity pool the cluster federates through"
  value       = "${var.project_id}.svc.id.goog"
}

output "kubeconfig_command" {
  description = "Point kubectl at this cluster"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --region ${var.region} --project ${var.project_id}"
}
