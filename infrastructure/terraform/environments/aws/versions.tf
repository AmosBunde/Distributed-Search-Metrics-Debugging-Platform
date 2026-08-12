terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0"
    }
  }

  # State lives in S3 with a DynamoDB lock table, both created by bootstrap.sh.
  # The values are supplied with -backend-config so this file holds no account
  # identifiers.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "search-metrics"
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = "Distributed-Search-Metrics-Debugging-Platform"
    }
  }
}
