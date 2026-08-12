output "cluster_name" {
  description = "Name of the AKS cluster"
  value       = azurerm_kubernetes_cluster.this.name
}

output "cluster_id" {
  description = "Resource ID of the cluster"
  value       = azurerm_kubernetes_cluster.this.id
}

output "oidc_issuer_url" {
  description = "OIDC issuer backing workload identity"
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "workload_identity_client_id" {
  description = "Client ID for serviceAccount.annotations.azure.workload.identity/client-id"
  value       = azurerm_user_assigned_identity.workload.client_id
}

output "workload_identity_principal_id" {
  description = "Principal ID, for granting the identity access to other resources"
  value       = azurerm_user_assigned_identity.workload.principal_id
}

output "kubelet_identity_object_id" {
  description = "Kubelet identity, which needs pull access to the registry"
  value       = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}

output "kubeconfig_command" {
  description = "Point kubectl at this cluster"
  value       = "az aks get-credentials --resource-group ${var.resource_group_name} --name ${azurerm_kubernetes_cluster.this.name}"
}
