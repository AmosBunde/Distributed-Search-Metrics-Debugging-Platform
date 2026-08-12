output "bootstrap_brokers" {
  description = "Plaintext bootstrap servers (in-VPC only)"
  value       = aws_msk_cluster.this.bootstrap_brokers
}

output "bootstrap_brokers_tls" {
  description = "TLS bootstrap servers — what the services should use"
  value       = aws_msk_cluster.this.bootstrap_brokers_tls
}

output "cluster_arn" {
  description = "ARN of the MSK cluster"
  value       = aws_msk_cluster.this.arn
}

output "security_group_id" {
  description = "Security group protecting the brokers"
  value       = aws_security_group.this.id
}

output "zookeeper_connect_string" {
  description = "ZooKeeper connection string, for tooling that still needs it"
  value       = aws_msk_cluster.this.zookeeper_connect_string
}
