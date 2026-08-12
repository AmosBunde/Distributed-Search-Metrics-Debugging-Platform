variable "name" {
  description = "Cluster name and resource prefix"
  type        = string
}

variable "kubernetes_version" {
  description = "EKS control plane version"
  type        = string
  default     = "1.29"
}

variable "vpc_id" {
  description = "VPC to place the cluster in"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the control plane and nodes"
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS key used to encrypt Kubernetes secrets at rest"
  type        = string
}

variable "endpoint_public_access" {
  description = "Expose the Kubernetes API publicly. Off by default, and requires explicit CIDRs."
  type        = bool
  default     = false
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach a public API endpoint. Never 0.0.0.0/0."
  type        = list(string)
  default     = []

  validation {
    # AWS defaults an enabled public endpoint to 0.0.0.0/0 when no CIDRs are
    # given, so an empty list plus public access is a world-readable Kubernetes
    # API by omission rather than by decision.
    condition     = !contains(var.public_access_cidrs, "0.0.0.0/0")
    error_message = "public_access_cidrs must not contain 0.0.0.0/0: name the networks that need access."
  }
}

variable "node_groups" {
  description = "Managed node groups, keyed by name"
  type = map(object({
    instance_types = list(string)
    capacity_type  = string
    disk_size      = number
    desired_size   = number
    min_size       = number
    max_size       = number
    labels         = map(string)
  }))

  default = {
    general = {
      instance_types = ["m5.2xlarge"]
      capacity_type  = "ON_DEMAND"
      disk_size      = 100
      desired_size   = 3
      min_size       = 3
      max_size       = 30
      labels         = { workload = "general" }
    }
  }
}

variable "log_retention_days" {
  description = "Retention for control plane logs"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
