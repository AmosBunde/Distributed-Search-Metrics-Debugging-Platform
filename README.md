# 🔍 Distributed Search Metrics & Debugging Platform

An internal developer tool that collects telemetry from multiple services, computes search-quality metrics (latency, relevance scores, error rates, anomaly detection), and helps engineers debug production issues quickly at large scale.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              Query Simulator  (realistic traffic gen)            │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP events
┌────────────────────────▼────────────────────────────────────────┐
│         Telemetry Collector  (Python / FastAPI)                  │
│   Validates · Enriches · Rate-limits · OTEL Tracing              │
└────────────────────────┬────────────────────────────────────────┘
                         │ publishes to
┌────────────────────────▼────────────────────────────────────────┐
│                  Apache Kafka  (Event Stream)                    │
│  search.events  search.errors  search.results  search.anomalies  │
└──────┬──────────────────────────────┬──────────────────────────┘
       │                              │
┌──────▼──────────────┐   ┌───────────▼──────────────────────────┐
│   Metrics Engine     │   │         Debug Service                │
│  - p50 / p95 / p99  │   │  - Trace storage (OTEL/Jaeger)       │
│  - Relevance scores │   │  - Root cause analysis               │
│  - Error rate       │   │  - Query replay API                  │
│  - Z-score anomaly  │   └──────────────────────────────────────┘
└──────┬──────────────┘
       │ writes
┌──────▼──────────────────────────────────────────────────────────┐
│    ClickHouse (OLAP)  PostgreSQL (meta)  Redis (hot cache)       │
│    S3 / Azure Blob / GCS  (raw telemetry archives)               │
└──────┬──────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│               API Gateway  (FastAPI + OpenAPI)                   │
│    /metrics  /anomalies  /debug  /traces  /replay                │
└──────┬──────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────┐
│    React Dashboard  (metrics · traces · anomalies · debug UI)    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Services | Python 3.11, FastAPI, OpenTelemetry |
| Stream Processing | Apache Flink (PyFlink) / PySpark |
| Event Stream | Apache Kafka |
| Analytics DB | ClickHouse |
| Metadata DB | PostgreSQL 15 |
| Cache | Redis 7 |
| Tracing | OpenTelemetry + Jaeger |
| Monitoring | Prometheus + Grafana |
| Object Storage | S3 / Azure Blob / GCS |
| Orchestration | Kubernetes + Helm |
| IaC | Terraform 1.7+ |
| CI/CD | GitHub Actions |

---

## Project Structure

```
search-metrics-platform/
├── services/
│   ├── telemetry-collector/     # Ingests search events → Kafka
│   ├── metrics-engine/          # Stream processing → ClickHouse
│   ├── debug-service/           # Trace storage & root cause analysis
│   ├── query-simulator/         # Realistic search traffic generator
│   └── api-gateway/             # REST API for dashboard & tooling
├── infrastructure/
│   └── terraform/
│       ├── modules/
│       │   ├── networking/      # VPC, subnets, security groups
│       │   ├── eks/             # AWS EKS
│       │   ├── aks/             # Azure AKS
│       │   ├── gke/             # GCP GKE
│       │   ├── clickhouse/      # ClickHouse EC2 cluster
│       │   ├── kafka/           # MSK / Event Hubs
│       │   └── monitoring/      # Prometheus + Grafana stack
│       └── environments/
│           ├── aws/
│           ├── azure/
│           └── gcp/
├── helm/                        # Kubernetes Helm chart
├── tests/
│   ├── unit/                    # No infra required
│   ├── integration/             # Requires docker compose
│   └── e2e/                     # Full black-box
├── scripts/                     # DB init SQL, seed data
├── .github/workflows/           # CI/CD pipeline
├── docker-compose.yml
└── Makefile
```

---

## Local Development

### Prerequisites
- Docker Desktop ≥ 4.20, Docker Compose v2
- Python 3.11+, Node.js 20+, Make

### 1. Clone & configure

```bash
git clone https://github.com/YOUR_USERNAME/search-metrics-platform.git
cd search-metrics-platform
cp .env.example .env
# Edit .env with your passwords
```

### 2. Start the full stack

```bash
make dev
```

| Service | URL |
|---|---|
| API Gateway | http://localhost:8000/docs |
| Grafana | http://localhost:3001 (admin/admin) |
| Jaeger Tracing | http://localhost:16686 |
| Kafka UI | http://localhost:8080 |
| ClickHouse Play | http://localhost:8123/play |
| Prometheus | http://localhost:9090 |

### 3. Generate search traffic

```bash
make simulate QPS=500
make simulate SCENARIO=error_spike QPS=1000
make simulate SCENARIO=slow_queries
make simulate SCENARIO=anomaly_spike
```

### 4. Verify data flowing

```bash
make health          # All services green?
make check-metrics   # ClickHouse getting data?
make check-kafka     # Kafka consumer lag?
```

---

## Deploy to AWS

### Prerequisites
- AWS CLI v2, Terraform 1.7+, kubectl, helm

### 1. Bootstrap Terraform state

```bash
cd infrastructure/terraform/environments/aws
chmod +x bootstrap.sh && ./bootstrap.sh
# Creates: S3 bucket + DynamoDB lock table
```

### 2. Configure

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit: aws_region, cluster_name, db_password, etc.
```

### 3. Deploy infrastructure

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**Provisions:**
- VPC (3 AZs) + NAT Gateways + VPC Endpoints (S3, ECR)
- EKS 1.29 + Managed Node Groups (m5.2xlarge, 3–30 nodes) + Cluster Autoscaler
- Amazon MSK (3-broker Kafka, lz4 compression, 7-day retention)
- RDS PostgreSQL 15 (Multi-AZ in prod, 7-day backups, Performance Insights)
- ElastiCache Redis (cluster mode)
- ClickHouse EC2 (r5.2xlarge, 1TB gp3 EBS, 4000 IOPS)
- S3 bucket with lifecycle (STANDARD_IA at 30d, Glacier at 90d)
- IAM roles with IRSA (least-privilege)

### 4. Deploy application

```bash
aws eks update-kubeconfig --name search-metrics-prod --region us-east-1
make build-push AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1
helm upgrade --install search-metrics ./helm \
  --namespace search-metrics --create-namespace \
  --values helm/values-aws.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
kubectl get pods -n search-metrics
```

---

## Deploy to Azure

### Prerequisites
- Azure CLI (`az login`), Terraform 1.7+, kubectl, helm

### 1. Create service principal

```bash
az ad sp create-for-rbac \
  --name "search-metrics-sp" \
  --role Contributor \
  --scopes /subscriptions/YOUR_SUBSCRIPTION_ID \
  --sdk-auth > azure-credentials.json

export ARM_CLIENT_ID=$(jq -r .clientId azure-credentials.json)
export ARM_CLIENT_SECRET=$(jq -r .clientSecret azure-credentials.json)
export ARM_SUBSCRIPTION_ID=$(jq -r .subscriptionId azure-credentials.json)
export ARM_TENANT_ID=$(jq -r .tenantId azure-credentials.json)
```

### 2. Deploy

```bash
cd infrastructure/terraform/environments/azure
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

**Provisions:** Resource Group + VNet + NSGs · AKS 1.29 (Standard_D8s_v3, 3–30 nodes) · Azure Event Hubs (Kafka-compatible, 20 TU auto-inflate) · PostgreSQL Flexible Server (ZoneRedundant HA in prod) · Azure Cache for Redis (Standard tier) · Azure Container Registry (Premium) · Blob Storage (GRS)

### 3. Deploy application

```bash
az aks get-credentials --resource-group search-metrics-rg --name search-metrics-aks
az acr login --name searchmetricsacr
make build-push ACR_NAME=searchmetricsacr
helm upgrade --install search-metrics ./helm \
  --values helm/values-azure.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

---

## Deploy to GCP

### Prerequisites
- `gcloud` CLI, Terraform 1.7+, kubectl, helm

### 1. Enable APIs

```bash
gcloud services enable container.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com pubsub.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com
```

### 2. Deploy

```bash
cd infrastructure/terraform/environments/gcp
cp terraform.tfvars.example terraform.tfvars
# Edit: project_id, region
terraform init && terraform apply
```

**Provisions:** VPC + private subnets + Cloud NAT · GKE Autopilot + node auto-provisioning · Cloud Pub/Sub (Kafka via connector, 7-day retention) · Cloud SQL PostgreSQL 15 (REGIONAL HA, PITR) · Memorystore Redis 7 (STANDARD_HA, TLS) · GCS (NEARLINE at 30d, COLDLINE at 90d) · Artifact Registry

### 3. Deploy application

```bash
gcloud container clusters get-credentials search-metrics-gke \
  --region us-central1 --project YOUR_PROJECT_ID
gcloud auth configure-docker us-central1-docker.pkg.dev
make build-push GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=us-central1
helm upgrade --install search-metrics ./helm \
  --values helm/values-gcp.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

---

## Running Tests

```bash
make test-unit          # Fast, no infrastructure needed
make test-integration   # Requires: make dev
make test-e2e           # Full black-box workflow
make coverage           # HTML report, target ≥80%
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/telemetry/event` | Ingest a single search event |
| POST | `/api/v1/telemetry/batch` | Ingest batch (up to 500 events) |
| GET | `/api/v1/metrics/latency` | Latency p50/p95/p99 by service |
| GET | `/api/v1/metrics/relevance` | Relevance score distribution |
| GET | `/api/v1/metrics/errors` | Error rates by service |
| GET | `/api/v1/metrics/summary` | Dashboard overview card |
| GET | `/api/v1/anomalies` | Detected anomalies feed |
| GET | `/api/v1/traces/{trace_id}` | Full distributed trace |
| GET | `/api/v1/debug/query/{query_id}` | Root cause debug info |
| POST | `/api/v1/debug/replay` | Replay a failed query |

Full OpenAPI docs: http://localhost:8000/docs

---

## Dashboards & Alerts

Grafana (http://localhost:3001) ships with pre-built dashboards:
- **Search Quality Overview** — latency heatmap, relevance trend, error rate timeline
- **Anomaly Detection** — Z-score spikes, volume anomalies
- **Service Health** — per-service SLO burn-down
- **Kafka Throughput** — consumer lag, partition distribution
- **ClickHouse Performance** — query latency, insert throughput

### Alert rules
| Alert | Threshold | Channel |
|---|---|---|
| p99 latency spike | > 2s for 5 min | PagerDuty |
| Error rate | > 1% | Slack #search-oncall |
| Anomaly score | > 3σ | Slack #search-quality |
| Kafka consumer lag | > 10,000 | Slack #search-oncall |
| ClickHouse disk | > 80% | PagerDuty |
