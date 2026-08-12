variable "name" {
  description = "Resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "VPC to place the servers in"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets to spread the servers across"
  type        = list(string)
}

variable "client_security_group_ids" {
  description = "Security groups allowed to query ClickHouse"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS key for the data volumes"
  type        = string
}

variable "instance_count" {
  description = "Number of ClickHouse servers"
  type        = number
  default     = 1
}

variable "instance_type" {
  description = "Instance type. Memory matters more than cores for aggregation."
  type        = string
  default     = "r5.2xlarge"
}

variable "data_volume_size_gb" {
  description = "Data volume size per server"
  type        = number
  default     = 1000
}

variable "data_volume_iops" {
  description = "Provisioned IOPS for the data volume"
  type        = number
  default     = 4000
}

variable "data_volume_throughput" {
  description = "Provisioned throughput (MiB/s). Scans want throughput, not IOPS."
  type        = number
  default     = 250
}

variable "clickhouse_version" {
  description = "ClickHouse package version"
  type        = string
  default     = "24.8.4.13"
}

variable "database" {
  description = "Database created on first boot"
  type        = string
  default     = "search_metrics"
}

variable "username" {
  description = "Application user the platform connects as"
  type        = string
  default     = "search"
}

variable "password_sha256_hex" {
  description = <<-DESC
    SHA-256 hex digest of the application user's password.

    The digest rather than the password: user data is readable through the
    instance metadata service by anything running on the box, so a plaintext
    credential there is a credential shared with every process on the host.
  DESC
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.password_sha256_hex))
    error_message = "password_sha256_hex must be a 64-character lowercase hex SHA-256 digest."
  }
}

variable "backup_bucket_arn" {
  description = "S3 bucket for backups. Empty disables backup permissions."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
