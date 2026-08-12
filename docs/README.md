# Documentation

| Document | Read it when |
|---|---|
| [Architecture decisions](adr/) | You want to know *why* something is the way it is |
| [Diagrams](diagrams/) | You want the picture, or want to change one |
| [Observability](observability.md) | You are wiring up alerts, or an alert fired |
| [Troubleshooting](troubleshooting.md) | Something will not start |
| [Contributing](../CONTRIBUTING.md) | You are about to open a pull request |
| [Tests](../tests/README.md) | You are writing a test, or one is flaky |

## Per component

| Component | README |
|---|---|
| Shared library | [`libs/common`](../libs/common/README.md) |
| Telemetry collector | [`services/telemetry-collector`](../services/telemetry-collector/README.md) |
| Metrics engine | [`services/metrics-engine`](../services/metrics-engine/README.md) |
| Debug service | [`services/debug-service`](../services/debug-service/README.md) |
| API gateway | [`services/api-gateway`](../services/api-gateway/README.md) |
| Query simulator | [`services/query-simulator`](../services/query-simulator/README.md) |
| Dashboard | [`services/dashboard`](../services/dashboard/README.md) |
| Helm chart | [`helm`](../helm/README.md) |
| AWS | [`infrastructure/terraform/environments/aws`](../infrastructure/terraform/environments/aws/README.md) |
| Azure | [`infrastructure/terraform/environments/azure`](../infrastructure/terraform/environments/azure/README.md) |
| GCP | [`infrastructure/terraform/environments/gcp`](../infrastructure/terraform/environments/gcp/README.md) |

## Where the interesting decisions live

Not everything worth knowing is an ADR. These are the choices most likely to
surprise someone reading the code:

- **Why a window is not emitted immediately** — the metrics engine's watermark
  and grace period ([`services/metrics-engine`](../services/metrics-engine/README.md))
- **Why offsets are committed last** — the same file, and the reason
  at-least-once actually holds
- **Why a slow span is not automatically a finding** — root cause analysis is
  measured against each service's own baseline
  ([`services/debug-service`](../services/debug-service/README.md))
- **Why replay has an allowlist** — it is the one outbound request a caller can
  influence, which makes it the one place SSRF is possible
- **Why the collector answers 429 instead of dropping** — the rate limiter fails
  open, because losing telemetry is worse than serving a burst
  ([`services/telemetry-collector`](../services/telemetry-collector/README.md))
