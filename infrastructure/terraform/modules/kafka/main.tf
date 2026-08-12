/**
 * Amazon MSK: the event backbone (ADR-0001).
 *
 * The configuration is the interesting part rather than the cluster itself:
 * auto topic creation is off so a typo fails loudly instead of silently
 * creating an empty topic, and retention gives an outage a recovery window.
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
  tags = merge(var.tags, { Module = "kafka" })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-msk"
  description = "MSK brokers"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Kafka TLS from the cluster"
    from_port       = 9094
    to_port         = 9094
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  ingress {
    description     = "Kafka plaintext from the cluster (in-VPC only)"
    from_port       = 9092
    to_port         = 9092
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  egress {
    description = "Broker egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${var.name}-msk" })
}

resource "aws_msk_configuration" "this" {
  name           = "${var.name}-config"
  kafka_versions = [var.kafka_version]

  server_properties = <<-PROPERTIES
    auto.create.topics.enable=false
    default.replication.factor=${min(var.broker_count, 3)}
    min.insync.replicas=${max(1, min(var.broker_count, 3) - 1)}
    num.partitions=${var.default_partitions}
    log.retention.hours=${var.retention_hours}
    compression.type=producer
    unclean.leader.election.enable=false
  PROPERTIES

  description = "Search metrics platform: no auto topic creation, no unclean leader election"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "broker" {
  name              = "/aws/msk/${var.name}"
  retention_in_days = var.log_retention_days
  tags              = local.tags
}

resource "aws_msk_cluster" "this" {
  cluster_name           = var.name
  kafka_version          = var.kafka_version
  number_of_broker_nodes = var.broker_count

  broker_node_group_info {
    instance_type   = var.instance_type
    client_subnets  = var.subnet_ids
    security_groups = [aws_security_group.this.id]

    storage_info {
      ebs_storage_info {
        volume_size = var.volume_size_gb
      }
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = var.kms_key_arn

    encryption_in_transit {
      client_broker = var.client_broker_encryption
      in_cluster    = true
    }
  }

  # Broker-level metrics: consumer lag per partition is the platform's main
  # back-pressure signal, and the default monitoring level does not report it.
  enhanced_monitoring = "PER_TOPIC_PER_PARTITION"

  open_monitoring {
    prometheus {
      jmx_exporter {
        enabled_in_broker = true
      }
      node_exporter {
        enabled_in_broker = true
      }
    }
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.broker.name
      }
    }
  }

  tags = local.tags
}
