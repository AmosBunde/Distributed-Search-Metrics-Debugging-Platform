/**
 * ClickHouse on EC2.
 *
 * There is no managed ClickHouse in the target clouds (ADR-0002), so this is
 * the one component the platform operates itself: instances, disks, backups and
 * disk-space alerts are ours.
 *
 * gp3 rather than gp2 because throughput and IOPS are provisioned independently
 * of size — the workload is few large inserts and heavy scans, which wants
 * throughput far more than capacity.
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
  tags = merge(var.tags, { Module = "clickhouse" })
}

data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_security_group" "this" {
  name        = "${var.name}-clickhouse"
  description = "ClickHouse servers"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTP interface from the cluster"
    from_port       = 8123
    to_port         = 8123
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  ingress {
    description     = "Native protocol from the cluster"
    from_port       = 9000
    to_port         = 9000
    protocol        = "tcp"
    security_groups = var.client_security_group_ids
  }

  ingress {
    description = "Inter-server replication"
    from_port   = 9009
    to_port     = 9009
    protocol    = "tcp"
    self        = true
  }

  egress {
    description = "Server egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${var.name}-clickhouse" })
}

resource "aws_iam_role" "this" {
  name = "${var.name}-clickhouse"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role = aws_iam_role.this.name
  # Session Manager instead of SSH: no key pairs, no port 22, and access is
  # audited.
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "backups" {
  count = var.backup_bucket_arn == "" ? 0 : 1

  name = "${var.name}-clickhouse-backups"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"]
      Resource = [var.backup_bucket_arn, "${var.backup_bucket_arn}/*"]
    }]
  })
}

resource "aws_iam_instance_profile" "this" {
  name = "${var.name}-clickhouse"
  role = aws_iam_role.this.name
  tags = local.tags
}

resource "aws_instance" "this" {
  count = var.instance_count

  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_ids[count.index % length(var.subnet_ids)]
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = aws_iam_instance_profile.this.name

  root_block_device {
    volume_type = "gp3"
    volume_size = 50
    encrypted   = true
  }

  metadata_options {
    # IMDSv2 only: an SSRF in anything running here must not be able to read
    # instance credentials.
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    clickhouse_version = var.clickhouse_version
    database           = var.database
    username           = var.username
    cluster_name       = var.name
    data_device        = "/dev/nvme1n1"
  })

  tags = merge(local.tags, { Name = "${var.name}-clickhouse-${count.index + 1}" })

  lifecycle {
    # Rebuilding on a new AMI would destroy the data volume with it. AMI
    # changes are handled as a deliberate rolling replacement.
    ignore_changes = [ami]
  }
}

resource "aws_ebs_volume" "data" {
  count = var.instance_count

  availability_zone = aws_instance.this[count.index].availability_zone
  size              = var.data_volume_size_gb
  type              = "gp3"
  iops              = var.data_volume_iops
  throughput        = var.data_volume_throughput
  encrypted         = true
  kms_key_id        = var.kms_key_arn

  tags = merge(local.tags, { Name = "${var.name}-clickhouse-data-${count.index + 1}" })
}

resource "aws_volume_attachment" "data" {
  count = var.instance_count

  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data[count.index].id
  instance_id = aws_instance.this[count.index].id
}

resource "aws_lb" "this" {
  count = var.instance_count > 1 ? 1 : 0

  name               = "${var.name}-clickhouse"
  internal           = true
  load_balancer_type = "network"
  subnets            = var.subnet_ids
  tags               = local.tags
}

resource "aws_lb_target_group" "http" {
  count = var.instance_count > 1 ? 1 : 0

  name     = "${var.name}-clickhouse-http"
  port     = 8123
  protocol = "TCP"
  vpc_id   = var.vpc_id

  health_check {
    protocol = "HTTP"
    path     = "/ping"
    port     = "8123"
  }

  tags = local.tags
}

resource "aws_lb_target_group_attachment" "http" {
  count = var.instance_count > 1 ? var.instance_count : 0

  target_group_arn = aws_lb_target_group.http[0].arn
  target_id        = aws_instance.this[count.index].id
  port             = 8123
}

resource "aws_lb_listener" "http" {
  count = var.instance_count > 1 ? 1 : 0

  load_balancer_arn = aws_lb.this[0].arn
  port              = 8123
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.http[0].arn
  }
}
