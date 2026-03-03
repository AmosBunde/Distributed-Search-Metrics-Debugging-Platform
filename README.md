# 🔍 Distributed Search Metrics & Debugging Platform

An internal engineering tool that collects telemetry from multiple services, computes real-time search-quality metrics (latency, relevance scores, error rates, anomaly detection), and helps engineers debug production issues quickly with trace-level visibility.

> Demonstrates distributed systems, telemetry pipelines, metrics engineering, reliability/on-call tooling, and developer productivity tooling at scale.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Local Development](#local-development)
- [Deploy to AWS](#deploy-to-aws)
- [Deploy to Azure](#deploy-to-azure)
- [Deploy to GCP](#deploy-to-gcp)
- [Running Tests](#running-tests)
- [API Reference](#api-reference)
- [Dashboards and Alerts](#dashboards-and-alerts)

---

## Architecture Overview

```
                  ┌────────────────────────────────────────┐
                  │         React Debug Dashboard           │
                  │  Latency · Relevance · Traces · Alerts  │
                  └──────────────────┬─────────────────────┘
                                     │ WebSocket + REST
            ┌────────────────────────▼────────────────────────┐
            │               Debug API (FastAPI)                │
            │     Trace Lookup · Metric Queries · Alerts       │
            └──────────┬──────────────────────┬───────────────┘
                       │                      │
      ┌────────────────▼──────┐  ┌────────────▼─────────────┐
      │   Metrics Engine      │  │   Anomaly Detector        │
      │   Faust/Kafka Streams │  │   z-score · EWMA · IQR   │
      └────────────┬──────────┘  └────────────┬─────────────┘
                   │                           │
      ┌────────────▼───────────────────────────▼─────────────┐
      │                    Apache Kafka                        │
      │  telemetry.raw · metrics.computed · alerts.fired      │
      └────────────────────────┬─────────────────────────────┘
                                │
      ┌────────────────────────▼──────────────┐
      │         Telemetry Collector            │
      │         OpenTelemetry-compatible       │
      │         Spans · Logs · Metrics ingestion│
      └────────────────────────┬──────────────┘
                                │
      ┌────────────────────────▼─────────────────────────────┐
      │                  Storage Layer                         │
      │  ClickHouse (analytics OLAP) · PostgreSQL (metadata)  │
      │  Redis (hot cache / recent traces)                     │
      └───────────────────────────────────────────────────────┘
      
      ┌──────────────────────────────────────────────────────┐
      │              Query Simulator                          │
      │   Generates realistic search traffic + traces         │
      │   ~500 req/s configurable load                        │
      └──────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| API | Python / FastAPI | REST + WebSocket debug API |
| Telemetry Ingest | Python / OpenTelemetry SDK | Collect spans, logs, metrics |
| Stream Processing | Python / Faust | Real-time Kafka-based metric computation |
| Anomaly Detection | Python / NumPy + scikit-learn | z-score, EWMA, IQR outlier detection |
| Traffic Simulator | Python / Locust | Synthetic search query generation |
| Analytics DB | ClickHouse | High-throughput OLAP queries on telemetry |
| Metadata DB | PostgreSQL 15 | Alerts, configs, service registry |
| Cache | Redis 7 | Hot metrics, recent traces, dedup |
| Event Stream | Apache Kafka | Telemetry backbone |
| Monitoring | Prometheus + Grafana | Platform self-monitoring + dashboards |
| Dashboard | React + TypeScript + Recharts | Engineer-facing debug UI |
| Orchestration | Kubernetes + Docker | Container runtime |
| IaC | Terraform 1.7+ | Cloud provisioning |
| CI/CD | GitHub Actions | Automated test and deploy pipeline |

---

## Project Structure

```
search-metrics-platform/
├── services/
│   ├── telemetry-collector/     # OpenTelemetry-compatible ingestion service
│   │   ├── app/
│   │   │   ├── main.py          # FastAPI OTLP receiver
│   │   │   ├── ingestor.py      # Span/log/metric normalisation
│   │   │   └── kafka_producer.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── metrics-engine/          # Faust stream processor
│   │   ├── app/
│   │   │   ├── app.py           # Faust app + topics
│   │   │   ├── processors.py    # Latency, relevance, error agents
│   │   │   └── sinks.py         # ClickHouse + Redis writers
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── query-simulator/         # Synthetic traffic generator
│   │   ├── app/
│   │   │   ├── simulator.py     # Locust + realistic query patterns
│   │   │   └── trace_builder.py # Distributed trace construction
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── debug-api/               # Main engineer-facing API
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── routers/
│   │   │   │   ├── traces.py
│   │   │   │   ├── metrics.py
│   │   │   │   ├── anomalies.py
│   │   │   │   └── alerts.py
│   │   │   ├── services/
│   │   │   │   ├── clickhouse.py
│   │   │   │   ├── redis_cache.py
│   │   │   │   └── websocket_manager.py
│   │   │   └── core/
│   │   │       └── config.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── anomaly-detector/        # Statistical + ML anomaly detection
│       ├── app/
│       │   ├── detector.py      # Faust agent consuming metrics
│       │   ├── algorithms/
│       │   │   ├── zscore.py
│       │   │   ├── ewma.py
│       │   │   └── iqr.py
│       │   └── publisher.py     # Publishes to alerts.fired topic
│       ├── Dockerfile
│       └── requirements.txt
├── infrastructure/
│   └── terraform/
│       ├── modules/
│       │   ├── networking/      # VPC, subnets, security groups
│       │   ├── eks/             # AWS EKS cluster module
│       │   ├── aks/             # Azure AKS cluster module
│       │   ├── gke/             # GCP GKE cluster module
│       │   ├── database/        # Cloud-native PostgreSQL
│       │   ├── clickhouse/      # ClickHouse on k8s (Helm)
│       │   ├── cache/           # Managed Redis
│       │   └── kafka/           # Managed Kafka per cloud
│       └── environments/
│           ├── aws/             # EKS + MSK + RDS + ElastiCache
│           ├── azure/           # AKS + Event Hubs + PostgreSQL + Redis
│           └── gcp/             # GKE + Pub/Sub + Cloud SQL + Memorystore
├── tests/
│   ├── unit/                    # Isolated unit tests per service
│   ├── integration/             # Cross-service pipeline tests
│   └── e2e/                     # Black-box platform tests
├── dashboard/                   # React + TypeScript
├── k8s/                         # Kustomize manifests
├── .github/workflows/           # CI/CD
├── docker-compose.yml
└── Makefile
```

---

## Local Development

### Prerequisites

- Docker Desktop >= 4.20
- Python 3.11+
- Node.js 20+
- Make

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/search-metrics-platform.git
cd search-metrics-platform
cp .env.example .env
```

### 2. Start the full stack

```bash
make dev
```

Services started:

| Service | URL |
|---|---|
| Debug API | http://localhost:8000/docs |
| React Dashboard | http://localhost:3000 |
| Grafana | http://localhost:3001 (admin/admin) |
| ClickHouse HTTP | http://localhost:8123 |
| Kafka UI | http://localhost:8080 |
| Prometheus | http://localhost:9090 |

### 3. Verify health

```bash
make health
```

### 4. Start the traffic simulator

```bash
make simulate
# Sends ~500 search req/s with realistic traces
```

### 5. View live data

Open http://localhost:3000 to see latency percentiles, relevance score distributions, active anomalies, and trace waterfalls.

---

## Deploy to AWS

### Prerequisites

- AWS CLI v2 configured (`aws configure`)
- Terraform 1.7+
- kubectl, helm

### 1. Bootstrap Terraform state

```bash
cd infrastructure/terraform/environments/aws
./bootstrap.sh
# Creates S3 bucket for state + DynamoDB lock table
```

### 2. Set variables

```bash
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars`:
```hcl
aws_region    = "us-east-1"
cluster_name  = "search-metrics-prod"
environment   = "production"
node_instance = "m5.2xlarge"
node_min      = 3
node_max      = 20
node_desired  = 5
db_instance   = "db.t3.large"
db_password   = "CHANGE_THIS_NOW"
```

### 3. Provision infrastructure

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

Provisioned resources:
- **VPC**: 3 AZs, public/private subnets, NAT Gateways, VPC endpoints (S3, ECR)
- **EKS 1.29**: Managed node groups, Cluster Autoscaler, OIDC/IRSA
- **MSK**: Kafka 3 brokers (`kafka.m5.large`), TLS, at-rest encryption
- **RDS PostgreSQL 15**: Multi-AZ, automated backups, KMS encryption
- **ElastiCache Redis 7**: Cluster mode, 3 shards, 2 replicas each
- **S3**: Telemetry archival with lifecycle (ClickHouse cold tier)
- **CloudWatch**: Log groups, composite alarms, dashboards

### 4. Configure kubectl and deploy

```bash
aws eks update-kubeconfig --region us-east-1 --name search-metrics-prod

# Build + push images
make build-push \
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) \
  AWS_REGION=us-east-1

# Deploy via Helm
helm upgrade --install search-metrics ./helm \
  --namespace search-metrics --create-namespace \
  --values helm/values-aws.yaml \
  --set image.tag=$(git rev-parse --short HEAD) \
  --wait --timeout=10m
```

### 5. Verify

```bash
kubectl get pods -n search-metrics
export API=$(kubectl get ingress -n search-metrics \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')
curl https://$API/health
```

---

## Deploy to Azure

### Prerequisites

- Azure CLI (`az login`)
- Terraform 1.7+

### 1. Create service principal

```bash
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
az ad sp create-for-rbac \
  --name "search-metrics-sp" \
  --role Contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID \
  --sdk-auth > azure-credentials.json

export ARM_CLIENT_ID=$(jq -r .clientId azure-credentials.json)
export ARM_CLIENT_SECRET=$(jq -r .clientSecret azure-credentials.json)
export ARM_SUBSCRIPTION_ID=$(jq -r .subscriptionId azure-credentials.json)
export ARM_TENANT_ID=$(jq -r .tenantId azure-credentials.json)
```

### 2. Deploy infrastructure

```bash
cd infrastructure/terraform/environments/azure
./bootstrap.sh
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

Provisioned:
- AKS with system + worker node pools, Workload Identity
- Azure Database for PostgreSQL Flexible Server (Zone-redundant HA)
- Azure Cache for Redis Premium (geo-replication)
- Azure Event Hubs with Kafka protocol support
- Azure Container Registry (Premium)
- Blob Storage (GRS) for telemetry archive

### 3. Configure kubectl and deploy

```bash
az aks get-credentials --resource-group search-metrics-rg --name search-metrics-aks
az acr login --name searchmetricsacr
make build-push ACR_NAME=searchmetricsacr
helm upgrade --install search-metrics ./helm \
  --namespace search-metrics --create-namespace \
  --values helm/values-azure.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

---

## Deploy to GCP

### Prerequisites

- gcloud CLI authenticated
- Terraform 1.7+

### 1. Enable APIs and authenticate

```bash
gcloud auth login && gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable \
  container.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com pubsub.googleapis.com \
  storage.googleapis.com artifactregistry.googleapis.com
```

### 2. Deploy infrastructure

```bash
cd infrastructure/terraform/environments/gcp
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

Provisioned:
- GKE Autopilot cluster with node auto-provisioning
- Cloud SQL PostgreSQL 15 (Regional HA)
- Memorystore Redis 7 (Standard HA)
- Pub/Sub topics (Kafka-compatible bridge)
- GCS bucket for telemetry archival
- Artifact Registry for containers

### 3. Configure kubectl and deploy

```bash
gcloud container clusters get-credentials search-metrics-gke \
  --region us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
make build-push GCP_PROJECT=YOUR_PROJECT_ID GCP_REGION=us-central1
helm upgrade --install search-metrics ./helm \
  --namespace search-metrics --create-namespace \
  --values helm/values-gcp.yaml
```

---

## Running Tests

### Unit Tests

```bash
make test-unit
# Per service:
cd services/metrics-engine  && pytest tests/unit -v
cd services/anomaly-detector && pytest tests/unit -v
cd services/debug-api        && pytest tests/unit -v
```

### Integration Tests

```bash
make dev                  # stack must be running
make test-integration
```

### End-to-End Tests

```bash
make test-e2e
# or against deployed:
E2E_BASE_URL=https://your-env.example.com make test-e2e
```

### Coverage

```bash
make coverage
# Target: 85%+ · Opens htmlcov/index.html
```

---

## API Reference

### Traces

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/traces/{trace_id}` | Full trace with all spans |
| GET | `/api/v1/traces?service=X&status=error` | Search/filter traces |
| GET | `/api/v1/traces/{trace_id}/waterfall` | Span waterfall for UI rendering |

### Metrics

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/metrics/latency?window=5m&p=99` | Latency percentiles |
| GET | `/api/v1/metrics/relevance` | Relevance score distribution |
| GET | `/api/v1/metrics/errors?window=1h` | Error rate by service |
| GET | `/api/v1/metrics/throughput` | Queries per second (QPS) |

### Anomalies

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/anomalies` | Active anomalies |
| GET | `/api/v1/anomalies/{id}` | Detail + surrounding context |
| POST | `/api/v1/anomalies/{id}/acknowledge` | Acknowledge for on-call |

### WebSocket

```
WS /ws/metrics     — Live metric stream (1s tick)
WS /ws/anomalies   — Real-time anomaly events
WS /ws/traces      — New failed trace stream
```

Full OpenAPI: http://localhost:8000/docs

---

## Dashboards and Alerts

### Pre-provisioned Grafana Dashboards

- **Search Latency Overview** — p50/p95/p99 per service + SLO burn rate
- **Relevance Score Distribution** — histogram by query type
- **Error Rate Heatmap** — error % by service and time window
- **Kafka Pipeline Health** — consumer lag, throughput, partition skew
- **ClickHouse Performance** — query times, insert rates, storage
- **Anomaly Timeline** — fired anomalies with context windows

### Default Alert Rules

| Alert | Condition | Severity |
|---|---|---|
| HighLatencyP99 | p99 > 500ms for 5m | warning |
| CriticalLatencyP99 | p99 > 2000ms for 2m | critical |
| HighErrorRate | error rate > 1% for 5m | warning |
| RelevanceScoreDrop | mean score < 0.6 for 10m | warning |
| KafkaConsumerLag | lag > 100k messages | warning |
| AnomalyFired | new anomaly detected | info |
