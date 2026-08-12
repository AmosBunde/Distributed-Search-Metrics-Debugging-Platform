variable "aws_region" {
  description = "Region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name. `prod` turns on multi-AZ and stricter retention."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "cluster_name" {
  description = "Name prefix for every resource"
  type        = string
  default     = "search-metrics"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "kubernetes_version" {
  description = "EKS control plane version"
  type        = string
  default     = "1.29"
}

variable "node_instance_types" {
  description = "Instance types for the general node group"
  type        = list(string)
  default     = ["m5.2xlarge"]
}

variable "node_min_size" {
  description = "Minimum nodes"
  type        = number
  default     = 3
}

variable "node_max_size" {
  description = "Maximum nodes the autoscaler may add"
  type        = number
  default     = 30
}

variable "kafka_broker_count" {
  description = "MSK brokers"
  type        = number
  default     = 3
}

variable "kafka_instance_type" {
  description = "MSK broker instance type"
  type        = string
  default     = "kafka.m5.large"
}

variable "clickhouse_instance_count" {
  description = "ClickHouse servers"
  type        = number
  default     = 1
}

variable "clickhouse_instance_type" {
  description = "ClickHouse instance type"
  type        = string
  default     = "r5.2xlarge"
}

variable "clickhouse_volume_size_gb" {
  description = "ClickHouse data volume size"
  type        = number
  default     = 1000
}

variable "db_password" {
  description = "PostgreSQL password. Supply via TF_VAR_db_password, never in a file."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 16
    error_message = "db_password must be at least 16 characters."
  }
}

variable "clickhouse_password" {
  description = "ClickHouse password. Supply via TF_VAR_clickhouse_password."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.clickhouse_password) >= 16
    error_message = "clickhouse_password must be at least 16 characters."
  }
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.medium"
}

variable "redis_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t4g.medium"
}

variable "alert_emails" {
  description = "Addresses subscribed to infrastructure alarms"
  type        = list(string)
  default     = []
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the Kubernetes API. Empty keeps it private."
  type        = list(string)
  default     = []
}
