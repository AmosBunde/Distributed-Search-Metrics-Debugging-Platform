# Helm chart

Deploys the five platform services and the dashboard into one namespace. It
never provisions Kafka, ClickHouse, PostgreSQL or Redis — those are managed
services created by Terraform, and the chart only takes their endpoints
([ADR-0005](../docs/adr/0005-multi-cloud-terraform-layout.md)).

```bash
helm upgrade --install search-metrics ./helm \
  --namespace search-metrics --create-namespace \
  --values helm/values-aws.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

## Before the first install

Credentials come from a Secret that already exists in the namespace. The chart
never renders one, so a password cannot end up in a values file, in git, or in
`helm get values` output:

```bash
kubectl create secret generic search-metrics-credentials \
  --namespace search-metrics \
  --from-literal=clickhouse-password='…' \
  --from-literal=postgres-password='…' \
  --from-literal=redis-password='…'
```

## What gets deployed

| Component | Replicas | Scaling |
|---|---|---|
| `collector` | 3 | HPA 3–30 on CPU |
| `engine` | 3 | **Manual** — see below |
| `debug` | 2 | Manual |
| `gateway` | 2 | HPA 2–10 on CPU |
| `dashboard` | 2 | Manual |

Each gets a Deployment, a Service, a PodDisruptionBudget, and a ServiceMonitor
when `serviceMonitor.enabled` is true.

**The engine is deliberately not autoscaled.** Its parallelism is bounded by
Kafka partition count, so an HPA would add pods that idle without consuming
anything ([ADR-0003](../docs/adr/0003-python-consumer-instead-of-flink.md)).
Scale it with partitions, together.

## Decisions worth knowing

**`image.tag` is required.** There is no default and no fallback to `latest`:
the chart fails to render without one. A mutable tag makes a rollback ambiguous
and a rolling restart non-deterministic.

**One workload template.** All four Python services render from a single
definition, so a difference between them is always a *values* difference and
therefore visible in review. Five near-identical Deployments drift.

**A startup probe, not a long liveness delay.** A slow start is a startup
problem; without a startup probe, a cold cache or a slow dependency lookup gets
the pod killed in a loop.

**Read-only root filesystem**, non-root user, no capabilities, and no
ServiceAccount token mounted — the services call no Kubernetes API.

**`/api` is routed before `/`** in the ingress, or the dashboard's catch-all
would swallow every API request.

## Per-cloud values

`values-aws.yaml`, `values-azure.yaml` and `values-gcp.yaml` differ only in
registry, managed-service endpoints, cloud identity annotations, ingress class
and resource shapes. Nothing about application behaviour is set there — a unit
test enforces that, because a threshold that differs between clouds is a bug
nobody finds until an incident.

Fill the endpoints from your Terraform outputs:

```bash
cd infrastructure/terraform/environments/aws
terraform output -raw msk_bootstrap_brokers
terraform output -raw rds_endpoint
terraform output -raw redis_endpoint
```

## Verifying a change

```bash
helm lint helm --set image.tag=test
helm template search-metrics helm --values helm/values-aws.yaml --set image.tag=test
```

CI runs both for every values file. The chart has never been applied to a real
cluster — rendering is not the same as running, and the first install should be
treated as a reviewed change.
