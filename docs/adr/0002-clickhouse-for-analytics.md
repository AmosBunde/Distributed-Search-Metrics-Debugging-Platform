# 0002. Use ClickHouse for search metrics storage

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The platform's read pattern is analytical, not transactional: percentile latency
per service over a time range, relevance distributions, error rates, anomaly
history. These queries scan large numbers of rows and aggregate them, and the
dashboard expects an answer in well under a second while telemetry keeps arriving.

The write pattern is append-only, high-volume, and never updated after the fact.

## Decision

We store raw events and rollups in ClickHouse, using `MergeTree` tables
partitioned by day, with a TTL that expires raw telemetry after 90 days.
PostgreSQL is kept for metadata that genuinely needs transactions.

## Consequences

### What this makes easy

- Percentile queries over hundreds of millions of rows return in milliseconds;
  `quantile` is a native aggregate, not a window-function workaround.
- Columnar compression makes retention affordable — telemetry compresses well
  because most columns repeat heavily.
- Partitioning by day makes expiry a metadata operation rather than a mass delete.

### What this makes hard

- Small frequent inserts degrade it badly, so the metrics engine must batch.
  This is a real constraint on the engine's design, not a tuning detail.
- No transactions and no meaningful uniqueness enforcement, so consumers must be
  idempotent by construction.
- Updates and deletes are asynchronous mutations, which rules ClickHouse out for
  anything mutable — hence PostgreSQL for replay jobs and alert state.

### What we now have to live with

ClickHouse has no managed offering in our Terraform targets, so it runs on
dedicated VMs with attached disks that we size, monitor and back up ourselves.
Disk usage is an alert (>80%) precisely because we own it.

## Alternatives considered

### PostgreSQL with TimescaleDB

Rejected. It would have removed a component, and Timescale handles time-series
well, but percentile queries over the volumes we expect are an order of magnitude
slower, and we would be sharing a database with the metadata workload.

### Elasticsearch

Rejected. Strong for text search and log exploration, but aggregation-heavy
numeric queries cost far more RAM per unit of data, and cluster operation is
heavier than the workload justifies.

### Prometheus as the metrics store

Rejected as the primary store. Prometheus is excellent for service-level
monitoring and we do use it for that, but it is dimensional and lossy by design:
it cannot answer "show me the trace for the slowest query of the hour", which is
the platform's whole point.

## Revisit when

Retention needs exceed a year, or a managed ClickHouse becomes available in all
three target clouds and removes the VM operations burden.
