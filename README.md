# Distributed Search Metrics & Debugging Platform

Collects telemetry from search services, turns it into metrics you can query,
detects anomalies against each service's own baseline, and answers the question
a dashboard cannot: **why was *this* query slow?**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/platform-architecture-dark.svg">
  <img alt="Platform architecture: a query simulator and instrumented services send telemetry to a collector, which publishes to Kafka; a metrics engine aggregates into ClickHouse; an API gateway serves a React dashboard; a debug service assembles traces for root cause analysis" src="docs/diagrams/platform-architecture-light.svg">
</picture>

> Every diagram here is generated from a checked-in specification and rendered
> by [Archify](https://github.com/tt-a1i/archify). Open the
> [interactive version](docs/diagrams/html/platform-architecture.html) to pan,
> zoom, search and trace a single path.

---

## Five minutes to running

**You need** Docker ≥ 24 with Compose v2, and about 6 GB of free memory.

```bash
git clone https://github.com/AmosBunde/Distributed-Search-Metrics-Debugging-Platform.git
cd Distributed-Search-Metrics-Debugging-Platform

cp .env.example .env     # every default works; nothing to edit yet
make dev                 # starts everything, waits until it is healthy
make simulate QPS=500    # generate realistic search traffic
make check-metrics       # confirm the data landed
```

Then open the dashboard at **<http://localhost:3000>**.

| What | Where |
|---|---|
| Dashboard | <http://localhost:3000> |
| API docs (OpenAPI) | <http://localhost:8000/docs> |
| Grafana | <http://localhost:3001> — dashboards already provisioned |
| Jaeger | <http://localhost:16686> |
| Kafka UI | <http://localhost:8080> |
| Prometheus | <http://localhost:9090> · alerts at `/alerts` |
| Alertmanager | <http://localhost:9093> |
| ClickHouse | <http://localhost:8123/play> |

**If `make dev` reports a port conflict**, it will tell you exactly which ports
are taken and which values to change in `.env`. Only the host side moves —
services still reach each other on standard ports inside the compose network.

```
6 host port(s) are already in use:
  8123   ClickHouse HTTP        held by python3
  5432   PostgreSQL

Every port is configurable. Edit .env and pick free ones:
  CLICKHOUSE_PORT=8124
  POSTGRES_PORT=5433
```

---

## What it does

### Ingest

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/telemetry-ingest-dark.svg">
  <img alt="Ingest pipeline: sources send telemetry to the collector, which validates and publishes to Kafka topics; the metrics engine consumes them into 60-second windows and writes rollups to ClickHouse and anomalies back to Kafka" src="docs/diagrams/telemetry-ingest-light.svg">
</picture>

Search services POST events to the **telemetry collector**, which validates,
enriches and publishes them to Kafka. The **metrics engine** consumes the
stream, aggregates it into 60-second windows per service, writes rollups to
ClickHouse, and scores each closed window against that service's recent
history.

A batch succeeds partially: one malformed event never costs you the other 499,
and the response names the entry and the field at fault.

### Debug

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/debug-request-dark.svg">
  <img alt="Sequence diagram: an operator opens a slow query in the dashboard, the gateway asks the debug service, which loads spans and a latency baseline, returns ranked findings, and can replay the query" src="docs/diagrams/debug-request-light.svg">
</picture>

Every finding carries the evidence that produced it and a confidence score, so
you can disagree with the analyser by looking at the same span it looked at:

```json
{
  "kind": "error_span",
  "summary": "index-service failed in fetch (error)",
  "confidence": 0.95,
  "evidence": { "status": "error", "duration_ms": 1200, "error": "shard timeout" }
}
```

A span is only "slow" if it is slow against **its own service's measured p95** —
900 ms in a service whose p95 is 850 ms is not a finding. A debugging tool that
always reports something is one people stop reading.

### Detect

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/anomaly-detection-dark.svg">
  <img alt="Anomaly detection workflow: consume a window, load a rolling baseline, compute a z-score, publish and route an alert, or suppress cold starts and duplicates" src="docs/diagrams/anomaly-detection-light.svg">
</picture>

A window is anomalous relative to its own recent history, not to a fixed
threshold. A service that normally answers in 800 ms is not broken because it
crossed 500 ms; one that normally answers in 5 ms very much is.

Three cases never produce an alert: a **cold start** (too little history), a
**flat baseline** (zero variance makes every z-score infinite), and a **tiny
window** (three queries say nothing about a service).

---

## The stack

| Layer | Technology |
|---|---|
| Services | Python 3.11, FastAPI, OpenTelemetry |
| Stream processing | Python Kafka consumer — [ADR-0003](docs/adr/0003-python-consumer-instead-of-flink.md) explains why not Flink |
| Event stream | Apache Kafka, 5 topics |
| Analytics store | ClickHouse |
| Metadata | PostgreSQL 15 |
| Cache and rate limiting | Redis 7 |
| Tracing | OpenTelemetry → Jaeger |
| Monitoring | Prometheus, Grafana, Alertmanager |
| Dashboard | React 18, TypeScript, Vite |
| Orchestration | Kubernetes via Helm |
| Infrastructure | Terraform 1.7+ for AWS, Azure and GCP |
| CI/CD | GitHub Actions |

### Layout

```
libs/common/            Models, settings, logging, tracing, Kafka helpers
services/
  telemetry-collector/  Ingest  :8001
  metrics-engine/       Windowing, rollups, anomaly detection  :8002
  debug-service/        Traces, root cause, replay  :8003
  api-gateway/          The only public surface  :8000
  query-simulator/      Traffic generation
  dashboard/            React UI  :3000
infrastructure/terraform/
  modules/              networking · eks · aks · gke · kafka · clickhouse · monitoring
  environments/         aws · azure · gcp
helm/                   One chart, three values files
docs/adr/               Why things are the way they are
tests/                  unit (no infra) · integration · e2e
```

---

## Working on it

```bash
make install-dev     # .venv with the shared library and every service dependency
make test-unit       # the unit suite: no infrastructure needed, seconds
make lint            # ruff check + format
make dev             # the full stack
make test-integration    # each hop of the pipeline, against the running stack
make test-e2e            # whole workflows through the public API
make coverage        # gated at 80%
make help            # every target, with a description
```

Targets that are not implemented yet fail loudly naming the issue that adds
them, so nothing silently does nothing.

### Making changes

The three suites are split by what they need, not by what they cover:

- **unit** — logic with dependencies faked. Percentiles, z-scores, window
  boundaries, trace assembly, replay diffing.
- **integration** — the joins the unit tests deliberately fake: a real broker,
  real SQL, real serialisation. This is the class of bug that unit tests
  structurally cannot catch.
- **e2e** — whole operator workflows through the public API only.

See [`tests/README.md`](tests/README.md), particularly the note about windows:
several tests timestamp events into a *past* window on purpose, because the
engine only emits a window once its watermark has passed it.

---

## Deploying

Infrastructure is Terraform; the application is Helm. They meet at a handful of
endpoints.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/deployment-topology-dark.svg">
  <img alt="Deployment topology: five stateless workloads in a Kubernetes namespace behind an ingress, talking to managed Kafka, ClickHouse, Postgres, Redis and object storage" src="docs/diagrams/deployment-topology-light.svg">
</picture>

> **None of the Terraform in this repository has ever been applied.** It is
> `fmt`-checked and `validate`-checked in CI, which catches syntax and type
> errors and nothing else. A first apply will surface quota limits, IAM
> propagation delays and provider differences that static validation cannot.
> Treat it as a reviewed change, not a formality. The Helm chart has likewise
> been rendered but never installed.

### AWS

```bash
cd infrastructure/terraform/environments/aws
./bootstrap.sh                       # state bucket + lock table, once per account
terraform init -backend-config=…     # bootstrap.sh prints the exact command

cp terraform.tfvars.example terraform.tfvars
export TF_VAR_db_password='…'        # never in a file
export TF_VAR_clickhouse_password='…'
export TF_VAR_redis_auth_token='…'

terraform plan -out=tfplan && terraform apply tfplan
```

Provisions a VPC across 3 AZs, EKS 1.29 with a private API endpoint, MSK, RDS
PostgreSQL, ElastiCache Redis with TLS, ClickHouse on EC2, an S3 bucket that
tiers, and IRSA roles. Full detail and cost estimates:
[`environments/aws/README.md`](infrastructure/terraform/environments/aws/README.md).

### Azure and GCP

Same platform, same module interfaces:
[Azure](infrastructure/terraform/environments/azure/README.md) ·
[GCP](infrastructure/terraform/environments/gcp/README.md).

One difference is worth knowing before you start: **Pub/Sub does not speak the
Kafka protocol.** Event Hubs does, so Azure needs no translation; GCP needs a
connector deployed before the platform, or ingest fails with connection errors
that look like a networking problem and are not.

### The application

```bash
kubectl create namespace search-metrics
kubectl create secret generic search-metrics-credentials \
  --namespace search-metrics \
  --from-literal=clickhouse-password='…' \
  --from-literal=postgres-password='…' \
  --from-literal=redis-password='…'

terraform output -raw helm_values_snippet > /tmp/endpoints.yaml

make build-push AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1

helm upgrade --install search-metrics ./helm \
  --namespace search-metrics \
  --values helm/values-aws.yaml \
  --values /tmp/endpoints.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

`image.tag` is required — the chart refuses to render without one, because a
mutable tag makes a rollback ambiguous. The chart never renders a Secret, so a
password cannot end up in a values file or in `helm get values` output.

---

## API

Full OpenAPI at <http://localhost:8000/docs>. An end-to-end test asserts that
every route in this table is actually served.

| Method | Endpoint | Returns |
|---|---|---|
| `POST` | `/api/v1/telemetry/event` | Ingest one event |
| `POST` | `/api/v1/telemetry/batch` | Ingest up to 500, with per-event results |
| `POST` | `/api/v1/telemetry/spans` | Ingest trace spans |
| `GET` | `/api/v1/metrics/latency` | p50/p95/p99 per bucket and service |
| `GET` | `/api/v1/metrics/relevance` | Relevance score distribution |
| `GET` | `/api/v1/metrics/errors` | Error rates |
| `GET` | `/api/v1/metrics/summary` | Overview card plus per-service rows |
| `GET` | `/api/v1/anomalies` | Detected anomalies with their evidence |
| `GET` | `/api/v1/queries/slowest` | The way into an investigation |
| `GET` | `/api/v1/traces/{trace_id}` | Assembled span tree and critical path |
| `GET` | `/api/v1/debug/query/{query_id}` | Ranked root cause findings |
| `POST` | `/api/v1/debug/replay` | Re-run a recorded query and diff it |

Common parameters: `minutes` (or `start`/`end`), `service`, `interval`
(`1m`,`5m`,`15m`,`1h`,`1d`), `limit`, `offset`, `severity`.

```bash
curl -X POST localhost:8000/api/v1/telemetry/event \
  -H 'content-type: application/json' \
  -d '{"query_id":"q-1","service":"search-api","query":"tracing","latency_ms":42.0}'

curl "localhost:8000/api/v1/metrics/latency?minutes=60&interval=5m"
```

---

## Traffic scenarios

```bash
make scenarios                                  # list them with their phases
make simulate QPS=500                           # steady, healthy traffic
make simulate SCENARIO=error_spike QPS=1000     # a dependency starts failing
make simulate SCENARIO=slow_queries             # latency degrades, nothing fails
make simulate SCENARIO=anomaly_spike            # exercises the detector
make simulate SCENARIO=traffic_drop             # volume falls off a cliff
```

`anomaly_spike` spends seven minutes on a calm baseline before it spikes,
because the detector needs history before it will report anything. A scenario
that spiked immediately would produce nothing and look broken.

---

## Alerting

Nine rules; every one waits before firing and every one says what to do.
Details in [`docs/observability.md`](docs/observability.md).

| Alert | Threshold | Goes to |
|---|---|---|
| p99 latency | > 2 s for 5 min | PagerDuty |
| Gateway error rate | > 1% for 5 min | Slack |
| Ingest rejections | > 5% for 10 min | Slack |
| Kafka consumer lag | > 10,000 for 10 min | Slack |
| Engine stalled | events arriving, none processed | PagerDuty |
| Service down | 2 min of failed scrapes | PagerDuty |

Locally, alerts fire and are visible at <http://localhost:9093> while notifying
nobody — Alertmanager does not expand environment variables, so the committed
config is valid on its own and a production example sits beside it.

---

## Decisions

The reasoning behind the shape of this system, including what was rejected:

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-kafka-event-backbone.md) | Kafka as the event backbone |
| [0002](docs/adr/0002-clickhouse-for-analytics.md) | ClickHouse for metrics storage |
| [0003](docs/adr/0003-python-consumer-instead-of-flink.md) | A Python consumer, not PyFlink, for v1 |
| [0004](docs/adr/0004-opentelemetry-tracing.md) | OpenTelemetry as the only tracing standard |
| [0005](docs/adr/0005-multi-cloud-terraform-layout.md) | One module set, three clouds |
| [0006](docs/adr/0006-archify-diagrams-as-svg.md) | Generated diagrams, committed as SVG |

More: [event lifecycle diagram](docs/diagrams/html/event-lifecycle.html) ·
[troubleshooting](docs/troubleshooting.md) ·
[observability](docs/observability.md) · [contributing](CONTRIBUTING.md)

---

## What is real, and what is not

Being straight about this is more useful than a longer feature list.

**Verified by running it.** The whole local stack, ingest through to the
dashboard; the unit, integration and end-to-end suites, all green in CI on
every push against a stack CI boots itself; the Grafana dashboards against live
traffic; and a fresh clone of this repository followed from `git clone` to a
working dashboard without a single undocumented step.

**Written and checked, never run.** All Terraform, for all three clouds, and
the Helm chart. `terraform validate` and `helm template` pass in CI; nothing has
ever been applied to a cloud account or installed into a cluster.

**Deliberately simplified.** The metrics engine is a Python consumer rather than
PyFlink — [ADR-0003](docs/adr/0003-python-consumer-instead-of-flink.md) sets out
the trade-off and the migration path. Spans reach ClickHouse through the
platform's own ingest path rather than an OpenTelemetry collector's exporter,
so an adopter has one ingest surface to point services at rather than two.

## Licence

No licence has been chosen yet. Until one is, default copyright applies.
