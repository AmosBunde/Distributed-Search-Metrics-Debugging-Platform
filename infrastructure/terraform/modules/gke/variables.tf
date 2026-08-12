variable "name" {
  description = "Cluster name and resource prefix"
  type        = string
}

variable "project_id" {
  description = "GCP project"
  type        = string
}

variable "region" {
  description = "Region for the regional cluster"
  type        = string
}

variable "network" {
  description = "VPC network self link or name"
  type        = string
}

variable "subnetwork" {
  description = "Subnetwork for the cluster"
  type        = string
}

variable "pods_range_name" {
  description = "Secondary range holding pod addresses"
  type        = string
}

variable "services_range_name" {
  description = "Secondary range holding service addresses"
  type        = string
}

variable "master_cidr" {
  description = "CIDR for the control plane. Must not overlap the VPC."
  type        = string
  default     = "172.16.0.0/28"
}

variable "private_endpoint" {
  description = "Keep the control plane endpoint off the public internet"
  type        = bool
  default     = false
}

variable "authorized_networks" {
  description = "Networks allowed to reach the control plane"
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []

  validation {
    condition     = alltrue([for network in var.authorized_networks : network.cidr_block != "0.0.0.0/0"])
    error_message = "authorized_networks must not contain 0.0.0.0/0: name the networks that need access."
  }
}

variable "release_channel" {
  description = "GKE release channel"
  type        = string
  default     = "REGULAR"
}

variable "deletion_protection" {
  description = "Refuse to delete the cluster through Terraform"
  type        = bool
  default     = true
}

variable "workload_namespace" {
  description = "Namespace of the federated service account"
  type        = string
  default     = "search-metrics"
}

variable "workload_service_account" {
  description = "Kubernetes service account the platform runs as"
  type        = string
  default     = "search-metrics"
}

variable "labels" {
  description = "Labels applied to the cluster"
  type        = map(string)
  default     = {}
}
