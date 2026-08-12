variable "location" {
  description = "Azure region"
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Environment name. `prod` turns on zone redundancy and longer retention."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "name" {
  description = "Name prefix for every resource"
  type        = string
  default     = "search-metrics"
}

variable "vnet_cidr" {
  description = "Address space for the virtual network"
  type        = string
  default     = "10.1.0.0/16"
}

variable "kubernetes_version" {
  description = "AKS version"
  type        = string
  default     = "1.29"
}

variable "node_vm_size" {
  description = "VM size for the node pool"
  type        = string
  default     = "Standard_D8s_v3"
}

variable "node_min_count" {
  description = "Minimum nodes"
  type        = number
  default     = 3
}

variable "node_max_count" {
  description = "Maximum nodes"
  type        = number
  default     = 30
}

variable "eventhub_capacity" {
  description = "Throughput units. Each is roughly 1 MB/s in, 2 MB/s out."
  type        = number
  default     = 4
}

variable "eventhub_max_capacity" {
  description = "Ceiling for auto-inflate"
  type        = number
  default     = 20
}

variable "postgres_sku" {
  description = "PostgreSQL Flexible Server SKU"
  type        = string
  default     = "GP_Standard_D2ds_v4"
}

variable "redis_capacity" {
  description = "Azure Cache for Redis capacity tier"
  type        = number
  default     = 1
}

variable "db_password" {
  description = "PostgreSQL password. Supply via TF_VAR_db_password."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 16
    error_message = "db_password must be at least 16 characters."
  }
}
