output "endpoint" {
  description = "Host the platform should connect to"
  value       = var.instance_count > 1 ? aws_lb.this[0].dns_name : aws_instance.this[0].private_ip
}

output "http_port" {
  description = "ClickHouse HTTP port"
  value       = 8123
}

output "instance_ids" {
  description = "IDs of the ClickHouse instances"
  value       = aws_instance.this[*].id
}

output "private_ips" {
  description = "Private IPs of the ClickHouse instances"
  value       = aws_instance.this[*].private_ip
}

output "security_group_id" {
  description = "Security group protecting the servers"
  value       = aws_security_group.this.id
}

output "data_volume_ids" {
  description = "Data volumes — what a snapshot policy should target"
  value       = aws_ebs_volume.data[*].id
}
