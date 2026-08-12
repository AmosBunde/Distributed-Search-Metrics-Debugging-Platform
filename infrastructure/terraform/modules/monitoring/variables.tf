variable "name" {
  description = "Resource prefix"
  type        = string
}

variable "region" {
  description = "Region the dashboard renders metrics from"
  type        = string
}

variable "kms_key_arn" {
  description = "KMS key for the alert topic"
  type        = string
}

variable "alert_emails" {
  description = "Addresses subscribed to infrastructure alerts"
  type        = list(string)
  default     = []
}

variable "alert_webhooks" {
  description = "HTTPS endpoints subscribed to alerts (PagerDuty, Slack relay)"
  type        = list(string)
  default     = []
}

variable "clickhouse_instance_ids" {
  description = "ClickHouse instances to alarm on"
  type        = list(string)
  default     = []
}

variable "msk_cluster_name" {
  description = "MSK cluster to alarm on. Empty disables the Kafka alarm."
  type        = string
  default     = ""
}

variable "disk_threshold_percent" {
  description = "Disk usage that triggers an alert"
  type        = number
  default     = 80
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default     = {}
}
