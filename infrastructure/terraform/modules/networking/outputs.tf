output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.this.cidr_block
}

output "private_subnet_ids" {
  description = "Private subnets — everything that holds data or state belongs here"
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnets — load balancers and NAT gateways only"
  value       = aws_subnet.public[*].id
}

output "availability_zones" {
  description = "Availability zones the subnets span"
  value       = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)
}

output "nat_gateway_ips" {
  description = "Public IPs traffic leaves from, for allowlisting upstream"
  value       = aws_eip.nat[*].public_ip
}
