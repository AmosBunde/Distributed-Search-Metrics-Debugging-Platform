variable "name" {
  description = "Cluster name and resource prefix"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the cluster in"
  type        = string
}

variable "kubernetes_version" {
  description = "AKS version"
  type        = string
  default     = "1.29"
}

variable "subnet_id" {
  description = "Subnet for the node pool"
  type        = string
}

variable "node_vm_size" {
  description = "VM size for the general node pool"
  type        = string
  default     = "Standard_D8s_v3"
}

variable "node_min_count" {
  description = "Minimum nodes"
  type        = number
  default     = 3
}

variable "node_max_count" {
  description = "Maximum nodes the autoscaler may add"
  type        = number
  default     = 30
}

variable "availability_zones" {
  description = "Zones to spread the node pool across"
  type        = list(string)
  default     = ["1", "2", "3"]
}

variable "private_cluster" {
  description = "Keep the Kubernetes API off the public internet"
  type        = bool
  default     = true
}

variable "service_cidr" {
  description = "CIDR for Kubernetes services. Must not overlap the VNet."
  type        = string
  default     = "10.100.0.0/16"
}

variable "dns_service_ip" {
  description = "Cluster DNS address, inside service_cidr"
  type        = string
  default     = "10.100.0.10"
}

variable "workload_namespace" {
  description = "Namespace of the service account that federates to the identity"
  type        = string
  default     = "search-metrics"
}

variable "workload_service_account" {
  description = "Service account name the platform runs as"
  type        = string
  default     = "search-metrics"
}

variable "log_retention_days" {
  description = "Retention for cluster logs"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
