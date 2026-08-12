# AWS environment

Provisions everything the platform needs on AWS. The application itself is
deployed separately with Helm; this creates the cluster and the managed services
it talks to.

## What it creates

| Resource | Notes |
|---|---|
| VPC across 3 AZs | Private subnets for everything, public for load balancers and NAT only |
| EKS 1.29 | Private API endpoint by default, managed node group, cluster autoscaler role |
| MSK (3 brokers) | No auto topic creation, no unclean leader election, 7-day retention |
| RDS PostgreSQL 15 | Multi-AZ in prod, encrypted, Performance Insights |
| ElastiCache Redis 7 | Replica and automatic failover in prod |
| ClickHouse on EC2 | gp3 with provisioned throughput, Session Manager access, no SSH |
| S3 | Versioned in prod, tiering to IA at 30 days and Glacier at 90 |
| KMS | One key for all platform data at rest |
| CloudWatch alarms | Disk and status checks — what fails *underneath* Prometheus |

## Deploying

```bash
# 1. State bucket and lock table (once per account and region)
./bootstrap.sh

# 2. Initialise with the backend it printed
terraform init -backend-config="bucket=…" -backend-config="key=…" …

# 3. Configure
cp terraform.tfvars.example terraform.tfvars   # gitignored
export TF_VAR_db_password='…'                  # never in a file
export TF_VAR_clickhouse_password='…'

# 4. Review, then apply
terraform plan -out=tfplan
terraform apply tfplan
```

Then wire the application to what was created:

```bash
aws eks update-kubeconfig --name search-metrics --region us-east-1
terraform output -raw helm_values_snippet > /tmp/values-endpoints.yaml

kubectl create namespace search-metrics
kubectl create secret generic search-metrics-credentials \
  --namespace search-metrics \
  --from-literal=clickhouse-password="$TF_VAR_clickhouse_password" \
  --from-literal=postgres-password="$TF_VAR_db_password"

helm upgrade --install search-metrics ../../../../helm \
  --namespace search-metrics \
  --values ../../../../helm/values-aws.yaml \
  --values /tmp/values-endpoints.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

## Decisions

**Passwords never reach a file.** They are `sensitive` variables supplied from
the environment, validated for length, and `terraform.tfvars` is gitignored.

**Production differs in durability, not in shape.** Multi-AZ, longer backups,
one NAT gateway per zone. A bug found in dev is a bug that exists in prod.

**One NAT gateway outside production.** A third of the cost, and egress dies
with that zone — an acceptable trade for a development environment and not for
a real one.

**The Kubernetes API is private by default.** Set `eks_public_access_cidrs` to
open it, and the variable's name makes it obvious you are doing so.

**IMDSv2 is required on the ClickHouse instances**, so a server-side request
forgery cannot be turned into instance credentials.

## Cost

The default shape is roughly **$1,400–1,800 per month** in `us-east-1`: EKS
control plane ~$73, three m5.2xlarge nodes ~$840, MSK three kafka.m5.large
~$390, one r5.2xlarge with 1 TB gp3 ~$460, RDS and ElastiCache ~$100 combined,
plus NAT gateways and data transfer. `environment = "dev"` with smaller
instances and a single NAT gateway brings it to roughly a third.

Check current pricing before believing these numbers.

## This has never been applied

It is `fmt`-checked and `validate`-checked in CI, which catches syntax and type
errors and nothing else. A first apply will surface quota limits, IAM
propagation delays and provider version differences that static validation
cannot. Treat it as a reviewed change, not a formality — and expect to iterate.
