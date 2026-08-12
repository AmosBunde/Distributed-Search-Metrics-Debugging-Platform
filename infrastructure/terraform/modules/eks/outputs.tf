output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = aws_eks_cluster.this.endpoint
}

output "cluster_certificate_authority" {
  description = "Base64 CA certificate for the API server"
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "cluster_security_group_id" {
  description = "Security group the control plane uses"
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN — the basis of every IRSA role"
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_provider_url" {
  description = "OIDC issuer URL without its scheme, for IRSA trust conditions"
  value       = replace(aws_iam_openid_connect_provider.this.url, "https://", "")
}

output "node_role_arn" {
  description = "IAM role the nodes assume"
  value       = aws_iam_role.node.arn
}

output "cluster_autoscaler_role_arn" {
  description = "Role for the cluster autoscaler's service account"
  value       = aws_iam_role.autoscaler.arn
}

output "kubeconfig_command" {
  description = "How to point kubectl at this cluster"
  value       = "aws eks update-kubeconfig --name ${aws_eks_cluster.this.name}"
}
