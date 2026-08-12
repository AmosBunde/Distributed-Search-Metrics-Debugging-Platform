variable "name" {
  description = "Name prefix for every resource in this module"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name))
    error_message = "name must be lowercase alphanumeric with hyphens, 2-31 characters."
  }
}

variable "region" {
  description = "AWS region, used to build VPC endpoint service names"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "availability_zone_count" {
  description = "Number of availability zones to span"
  type        = number
  default     = 3

  validation {
    condition     = var.availability_zone_count >= 2 && var.availability_zone_count <= 6
    error_message = "availability_zone_count must be between 2 and 6."
  }
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway instead of one per AZ. Cheaper, and egress dies with that zone."
  type        = bool
  default     = false
}

variable "interface_endpoints" {
  description = "AWS services to reach through interface endpoints rather than the NAT gateways"
  type        = list(string)
  default     = ["ecr.api", "ecr.dkr", "logs", "sts"]
}

variable "enable_flow_logs" {
  description = "Record rejected traffic. The first thing anyone wants during an incident."
  type        = bool
  default     = true
}

variable "flow_log_retention_days" {
  description = "How long to keep VPC flow logs"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
