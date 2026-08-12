output "alert_topic_arn" {
  description = "SNS topic infrastructure alarms publish to"
  value       = aws_sns_topic.alerts.arn
}

output "dashboard_name" {
  description = "CloudWatch dashboard name"
  value       = aws_cloudwatch_dashboard.platform.dashboard_name
}
