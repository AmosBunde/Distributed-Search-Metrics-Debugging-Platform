variable "name" {
  description = "Cluster name and resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "VPC to place the brokers in"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets, one per broker"
  type        = list(string)
}

variable "client_security_group_ids" {
  description = "Security groups allowed to reach the brokers"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS key for encryption at rest"
  type        = string
}

variable "kafka_version" {
  description = "Kafka version"
  type        = string
  default     = "3.6.0"
}

variable "broker_count" {
  description = "Number of brokers. Must be a multiple of the subnet count."
  type        = number
  default     = 3

  validation {
    condition     = var.broker_count >= 2
    error_message = "broker_count must be at least 2; a single broker cannot replicate."
  }
}

variable "instance_type" {
  description = "Broker instance type"
  type        = string
  default     = "kafka.m5.large"
}

variable "volume_size_gb" {
  description = "EBS volume per broker. Must hold retention_hours of ingest."
  type        = number
  default     = 500
}

variable "default_partitions" {
  description = "Default partition count. Caps consumer parallelism (ADR-0001)."
  type        = number
  default     = 6
}

variable "retention_hours" {
  description = "How long records are kept — the size of an outage recovery window"
  type        = number
  default     = 168
}

variable "client_broker_encryption" {
  description = "TLS, TLS_PLAINTEXT or PLAINTEXT for client connections"
  type        = string
  default     = "TLS"

  validation {
    condition     = contains(["TLS", "TLS_PLAINTEXT", "PLAINTEXT"], var.client_broker_encryption)
    error_message = "client_broker_encryption must be TLS, TLS_PLAINTEXT or PLAINTEXT."
  }
}

variable "log_retention_days" {
  description = "Retention for broker logs"
  type        = number
  default     = 14
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
