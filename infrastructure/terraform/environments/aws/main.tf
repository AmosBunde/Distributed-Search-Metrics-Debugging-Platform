/**
 * AWS environment for the search metrics platform.
 *
 * Composes the shared modules and adds the managed services that have no module
 * of their own because they are a single resource each: RDS, ElastiCache, S3.
 *
 * Production differs from development in durability rather than in shape —
 * multi-AZ, longer backups, one NAT gateway per zone — so a bug found in dev is
 * a bug that exists in prod.
 */

locals {
  name     = var.cluster_name
  is_prod  = var.environment == "prod"
  az_count = 3
  common_tags = {
    Environment = var.environment
    Component   = "search-metrics"
  }
}

# One key for the platform's data at rest: EKS secrets, MSK, RDS, ClickHouse
# volumes, S3 and the alert topic. One key means one rotation policy and one
# place to audit access.
resource "aws_kms_key" "platform" {
  description             = "${local.name} platform encryption"
  deletion_window_in_days = local.is_prod ? 30 : 7
  enable_key_rotation     = true
  tags                    = local.common_tags
}

resource "aws_kms_alias" "platform" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.platform.key_id
}

module "networking" {
  source = "../../modules/networking"

  name                    = local.name
  region                  = var.aws_region
  vpc_cidr                = var.vpc_cidr
  availability_zone_count = local.az_count
  # One NAT gateway outside production: a third of the cost, and egress dies
  # with that zone.
  single_nat_gateway      = !local.is_prod
  enable_flow_logs        = true
  flow_log_retention_days = local.is_prod ? 90 : 14

  tags = local.common_tags
}

module "eks" {
  source = "../../modules/eks"

  name               = local.name
  kubernetes_version = var.kubernetes_version
  vpc_id             = module.networking.vpc_id
  subnet_ids         = module.networking.private_subnet_ids
  kms_key_arn        = aws_kms_key.platform.arn

  endpoint_public_access = length(var.eks_public_access_cidrs) > 0
  public_access_cidrs    = var.eks_public_access_cidrs

  node_groups = {
    general = {
      instance_types = var.node_instance_types
      capacity_type  = "ON_DEMAND"
      disk_size      = 100
      desired_size   = var.node_min_size
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      labels         = { workload = "general" }
    }
  }

  log_retention_days = local.is_prod ? 90 : 14
  tags               = local.common_tags
}

module "kafka" {
  source = "../../modules/kafka"

  name                      = local.name
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  client_security_group_ids = [module.eks.cluster_security_group_id]
  kms_key_arn               = aws_kms_key.platform.arn

  broker_count    = var.kafka_broker_count
  instance_type   = var.kafka_instance_type
  retention_hours = 168
  tags            = local.common_tags
}

module "clickhouse" {
  source = "../../modules/clickhouse"

  name                      = local.name
  vpc_id                    = module.networking.vpc_id
  subnet_ids                = module.networking.private_subnet_ids
  client_security_group_ids = [module.eks.cluster_security_group_id]
  kms_key_arn               = aws_kms_key.platform.arn

  instance_count      = var.clickhouse_instance_count
  instance_type       = var.clickhouse_instance_type
  data_volume_size_gb = var.clickhouse_volume_size_gb
  password_sha256_hex = sha256(var.clickhouse_password)
  backup_bucket_arn   = aws_s3_bucket.telemetry.arn
  tags                = local.common_tags
}

module "monitoring" {
  source = "../../modules/monitoring"

  name        = local.name
  region      = var.aws_region
  kms_key_arn = aws_kms_key.platform.arn

  alert_emails            = var.alert_emails
  clickhouse_instance_ids = module.clickhouse.instance_ids
  msk_cluster_name        = local.name
  tags                    = local.common_tags
}

# --- PostgreSQL -------------------------------------------------------------

resource "aws_db_subnet_group" "postgres" {
  name       = "${local.name}-postgres"
  subnet_ids = module.networking.private_subnet_ids
  tags       = local.common_tags
}

resource "aws_security_group" "postgres" {
  name        = "${local.name}-postgres"
  description = "PostgreSQL"
  vpc_id      = module.networking.vpc_id

  ingress {
    description     = "PostgreSQL from the cluster"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.cluster_security_group_id]
  }

  tags = merge(local.common_tags, { Name = "${local.name}-postgres" })
}

resource "aws_db_instance" "postgres" {
  identifier     = "${local.name}-postgres"
  engine         = "postgres"
  engine_version = "15"
  instance_class = var.db_instance_class

  allocated_storage     = 50
  max_allocated_storage = 500
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.platform.arn

  db_name  = "search_metrics_meta"
  username = "search"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.postgres.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  publicly_accessible    = false

  multi_az                = local.is_prod
  backup_retention_period = local.is_prod ? 7 : 1
  backup_window           = "03:00-04:00"
  maintenance_window      = "sun:04:00-sun:05:00"

  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.platform.arn
  enabled_cloudwatch_logs_exports = ["postgresql"]

  auto_minor_version_upgrade = true
  deletion_protection        = local.is_prod
  skip_final_snapshot        = !local.is_prod
  final_snapshot_identifier  = local.is_prod ? "${local.name}-postgres-final" : null

  tags = local.common_tags
}

# --- Redis ------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name}-redis"
  subnet_ids = module.networking.private_subnet_ids
  tags       = local.common_tags
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis"
  vpc_id      = module.networking.vpc_id

  ingress {
    description     = "Redis from the cluster"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.cluster_security_group_id]
  }

  tags = merge(local.common_tags, { Name = "${local.name}-redis" })
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${local.name}-redis"
  description          = "Search metrics hot cache and rate limiter"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  # The cache is derived data, but the rate limiter's buckets are not: losing
  # them lets a burst through, so production runs a replica.
  num_cache_clusters         = local.is_prod ? 2 : 1
  automatic_failover_enabled = local.is_prod
  multi_az_enabled           = local.is_prod

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  kms_key_id                 = aws_kms_key.platform.arn
  # Rate-limit buckets and cached query results cross the network in both
  # directions; a VPC is not a trust boundary on its own.
  transit_encryption_enabled = true
  auth_token                 = var.redis_auth_token
  auth_token_update_strategy = "ROTATE"

  snapshot_retention_limit = local.is_prod ? 5 : 0
  maintenance_window       = "sun:05:00-sun:06:00"

  tags = local.common_tags
}

# --- Object storage ---------------------------------------------------------

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "telemetry" {
  bucket = "${local.name}-telemetry-${random_id.bucket_suffix.hex}"
  tags   = local.common_tags
}

resource "aws_s3_bucket_public_access_block" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.platform.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id

  versioning_configuration {
    status = local.is_prod ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "telemetry" {
  bucket = aws_s3_bucket.telemetry.id

  # Raw telemetry is read constantly for a week, occasionally for a month, and
  # then almost never — so it tiers rather than being deleted.
  rule {
    id     = "tier-and-expire"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 365
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# --- Application identity (IRSA) --------------------------------------------

data "aws_iam_policy_document" "platform_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:sub"
      values   = ["system:serviceaccount:search-metrics:search-metrics"]
    }

    condition {
      test     = "StringEquals"
      variable = "${module.eks.oidc_provider_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "platform" {
  name               = "${local.name}-irsa"
  assume_role_policy = data.aws_iam_policy_document.platform_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "platform" {
  name = "${local.name}-platform"
  role = aws_iam_role.platform.id

  # Least privilege: the services archive telemetry and read their own metrics.
  # Nothing here can create infrastructure.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "TelemetryArchive"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.telemetry.arn, "${aws_s3_bucket.telemetry.arn}/*"]
      },
      {
        Sid      = "EncryptArchive"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.platform.arn]
      },
      {
        Sid      = "PublishOwnMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "SearchMetrics" }
        }
      },
    ]
  })
}
