# GCP environment

The same platform again, in GCP's vocabulary: Pub/Sub for the event stream,
Cloud SQL for PostgreSQL, Memorystore for Redis, GKE Autopilot for compute.

## What it creates

| Resource | Notes |
|---|---|
| VPC, subnet, Cloud NAT | Secondary ranges for pods and services; private Google access |
| GKE Autopilot | Regional, private nodes, workload identity |
| Pub/Sub | Five topics with 7-day retention and a subscription per topic |
| Cloud SQL PostgreSQL 15 | Private IP only, regional HA and PITR in prod |
| Memorystore Redis 7 | TLS and AUTH, STANDARD_HA in prod |
| Artifact Registry | Docker repository for the platform images |
| GCS | NEARLINE at 30 days, COLDLINE at 90, deleted at a year |

## Deploying

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable container.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com pubsub.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com servicenetworking.googleapis.com

gsutil mb -l us-central1 gs://YOUR_PROJECT-tfstate
terraform init -backend-config="bucket=YOUR_PROJECT-tfstate" \
               -backend-config="prefix=search-metrics"

cp terraform.tfvars.example terraform.tfvars
export TF_VAR_db_password='…'

terraform plan -out=tfplan
terraform apply tfplan
```

## The one real difference: Pub/Sub is not Kafka

The platform speaks the Kafka protocol. Event Hubs does too, so Azure needs no
translation; Pub/Sub does not. A **Kafka connector** bridges the two, which is
why `values-gcp.yaml` points `bootstrapServers` at a connector service inside
the cluster rather than at a managed endpoint.

The alternative would be a Pub/Sub client in the application, which would mean
the ingest path behaves differently on one cloud — exactly what ADR-0005 exists
to prevent. Deploy the connector before the platform, or ingest fails with
connection errors that look like a networking problem and are not.

**Autopilot sizes nodes from pod requests**, so `values-gcp.yaml` sets requests
and limits deliberately: on Autopilot those numbers are the bill.

## This has never been applied

Same caveat as AWS and Azure. `fmt` and `validate` catch syntax and type errors
and nothing else. Expect a first apply to surface API enablement, quota and
service-networking peering issues that static validation cannot see.
