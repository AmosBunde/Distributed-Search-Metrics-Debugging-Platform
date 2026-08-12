#!/usr/bin/env bash
# Create the S3 bucket and DynamoDB table that hold Terraform's state and lock.
#
# This runs once per account and region, before the first `terraform init`.
# State cannot live in the state it manages, which is why this is a script and
# not a Terraform module.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${CLUSTER_NAME:-search-metrics}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${PREFIX}-tfstate-${ACCOUNT_ID}"
TABLE="${PREFIX}-tflock"

echo "Account:  ${ACCOUNT_ID}"
echo "Region:   ${REGION}"
echo "Bucket:   ${BUCKET}"
echo "Table:    ${TABLE}"
echo

if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "✓ state bucket already exists"
else
    echo "→ creating state bucket"
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
    else
        aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
            --create-bucket-configuration "LocationConstraint=${REGION}"
    fi

    # Versioning first: it is the only thing that makes a corrupted or
    # accidentally deleted state recoverable.
    aws s3api put-bucket-versioning --bucket "$BUCKET" \
        --versioning-configuration Status=Enabled
    aws s3api put-bucket-encryption --bucket "$BUCKET" \
        --server-side-encryption-configuration \
        '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    aws s3api put-public-access-block --bucket "$BUCKET" \
        --public-access-block-configuration \
        'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
fi

if aws dynamodb describe-table --table-name "$TABLE" --region "$REGION" >/dev/null 2>&1; then
    echo "✓ lock table already exists"
else
    echo "→ creating lock table"
    aws dynamodb create-table \
        --table-name "$TABLE" \
        --region "$REGION" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST >/dev/null
    aws dynamodb wait table-exists --table-name "$TABLE" --region "$REGION"
fi

cat <<NEXT

Bootstrap complete. Initialise Terraform with:

  terraform init \\
    -backend-config="bucket=${BUCKET}" \\
    -backend-config="key=${PREFIX}/terraform.tfstate" \\
    -backend-config="region=${REGION}" \\
    -backend-config="dynamodb_table=${TABLE}" \\
    -backend-config="encrypt=true"

NEXT
