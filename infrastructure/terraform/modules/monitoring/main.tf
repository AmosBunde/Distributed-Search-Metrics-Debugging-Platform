/**
 * Alerting for the things Prometheus cannot see.
 *
 * Application alerts live in Prometheus rules next to the metrics they watch.
 * What belongs here is infrastructure that fails *underneath* the cluster —
 * ClickHouse running out of disk, MSK filling up — because when those break,
 * the Prometheus that would have alerted on them is often broken too.
 */

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

locals {
  tags = merge(var.tags, { Module = "monitoring" })
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name}-alerts"
  kms_master_key_id = var.kms_key_arn
  tags              = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_sns_topic_subscription" "https" {
  for_each = toset(var.alert_webhooks)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "https"
  endpoint  = each.value
}

# ClickHouse disk is the platform's most predictable outage: it fills, inserts
# start failing, and the metrics that would have warned you stop being written.
resource "aws_cloudwatch_metric_alarm" "clickhouse_disk" {
  for_each = toset(var.clickhouse_instance_ids)

  alarm_name          = "${var.name}-clickhouse-disk-${each.value}"
  alarm_description   = "ClickHouse data volume above ${var.disk_threshold_percent}% on ${each.value}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = var.disk_threshold_percent
  period              = 300
  statistic           = "Average"
  namespace           = "CWAgent"
  metric_name         = "disk_used_percent"

  dimensions = {
    InstanceId = each.value
    path       = "/var/lib/clickhouse"
  }

  alarm_actions             = [aws_sns_topic.alerts.arn]
  ok_actions                = [aws_sns_topic.alerts.arn]
  treat_missing_data        = "breaching"
  insufficient_data_actions = [aws_sns_topic.alerts.arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "clickhouse_status" {
  for_each = toset(var.clickhouse_instance_ids)

  alarm_name          = "${var.name}-clickhouse-status-${each.value}"
  alarm_description   = "ClickHouse instance ${each.value} failed its status check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 0
  period              = 60
  statistic           = "Maximum"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"

  dimensions    = { InstanceId = each.value }
  alarm_actions = [aws_sns_topic.alerts.arn]
  tags          = local.tags
}

resource "aws_cloudwatch_metric_alarm" "kafka_disk" {
  count = var.msk_cluster_name == "" ? 0 : 1

  alarm_name          = "${var.name}-kafka-disk"
  alarm_description   = "MSK broker storage above ${var.disk_threshold_percent}%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = var.disk_threshold_percent
  period              = 300
  statistic           = "Maximum"
  namespace           = "AWS/Kafka"
  metric_name         = "KafkaDataLogsDiskUsed"

  dimensions    = { "Cluster Name" = var.msk_cluster_name }
  alarm_actions = [aws_sns_topic.alerts.arn]
  tags          = local.tags
}

resource "aws_cloudwatch_dashboard" "platform" {
  dashboard_name = "${var.name}-platform"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "MSK broker CPU"
          region = var.region
          metrics = [
            ["AWS/Kafka", "CpuUser", "Cluster Name", var.msk_cluster_name],
          ]
          period = 300
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "ClickHouse CPU"
          region = var.region
          metrics = [
            for id in var.clickhouse_instance_ids :
            ["AWS/EC2", "CPUUtilization", "InstanceId", id]
          ]
          period = 300
        }
      },
    ]
  })
}
