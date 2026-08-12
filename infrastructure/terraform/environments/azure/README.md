# Azure environment

The same platform as the AWS environment, in Azure's vocabulary: Event Hubs
speaks the Kafka protocol, Flexible Server is PostgreSQL, Azure Cache is Redis.

## What it creates

| Resource | Notes |
|---|---|
| Resource group, VNet, NSGs | Data subnet denies everything the cluster subnet does not need |
| AKS 1.29 | Private cluster, workload identity, autoscaling node pool across 3 zones |
| Event Hubs (Standard) | Kafka protocol on 9093, auto-inflate to 20 TU, five topics |
| PostgreSQL Flexible Server | Delegated subnet, no public access, zone-redundant HA in prod |
| Azure Cache for Redis | TLS only — the non-TLS port is disabled |
| Container Registry (Premium) | Kubelet identity granted AcrPull; admin user disabled |
| Storage Account | GRS in prod, tiering to cool at 30 days and archive at 90 |

## Deploying

```bash
az login
az account set --subscription YOUR_SUBSCRIPTION_ID

# State storage (once per subscription)
az group create --name terraform-state --location eastus
az storage account create --name tfstate$RANDOM --resource-group terraform-state \
  --sku Standard_LRS --encryption-services blob
az storage container create --name tfstate --account-name <account>

terraform init \
  -backend-config="resource_group_name=terraform-state" \
  -backend-config="storage_account_name=<account>" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=search-metrics.tfstate"

cp terraform.tfvars.example terraform.tfvars
export TF_VAR_db_password='…'

terraform plan -out=tfplan
terraform apply tfplan
```

Then:

```bash
az aks get-credentials --resource-group search-metrics-rg --name search-metrics
az acr login --name searchmetricsacr
make build-push ACR_NAME=searchmetricsacr

terraform output -raw helm_values_snippet > /tmp/values-endpoints.yaml
helm upgrade --install search-metrics ../../../../helm \
  --namespace search-metrics --create-namespace \
  --values ../../../../helm/values-azure.yaml \
  --values /tmp/values-endpoints.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

## Differences from AWS worth knowing

**Event Hubs is Kafka-compatible, not Kafka.** It speaks the protocol on port
9093 with SASL over TLS, and topics are Event Hubs entities created by
Terraform rather than by the platform's own topic script. Consumer group
semantics differ subtly under heavy rebalancing.

**PostgreSQL is injected into a delegated subnet** rather than reached through
a private endpoint, which is why the data subnet exists and is delegated.

**Redis has no non-TLS port**, so `dependencies.redis.tls` must stay true and
the port is 6380.

## This has never been applied

Same caveat as AWS: `fmt` and `validate` catch syntax and type errors and
nothing else. A first apply will surface quota limits, name-uniqueness
collisions (storage accounts and ACR names are globally unique) and provider
differences that static validation cannot.
