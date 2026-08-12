variable "project_id" {
  description = "GCP project to deploy into"
  type        = string
}

variable "region" {
  description = "Region for the regional cluster and managed services"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name. `prod` turns on regional HA and PITR."
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

variable "subnet_cidr" {
  description = "Primary CIDR for the cluster subnet"
  type        = string
  default     = "10.2.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pod addresses"
  type        = string
  default     = "10.4.0.0/14"
}

variable "services_cidr" {
  description = "Secondary range for service addresses"
  type        = string
  default     = "10.8.0.0/20"
}

variable "authorized_networks" {
  description = "Networks allowed to reach the Kubernetes control plane"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "postgres_tier" {
  description = "Cloud SQL machine type"
  type        = string
  default     = "db-custom-2-7680"
}

variable "redis_memory_gb" {
  description = "Memorystore capacity"
  type        = number
  default     = 4
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
