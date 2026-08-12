# 0003. Use a Python Kafka consumer, not PyFlink, for v1

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The metrics engine performs windowed aggregation over a Kafka stream: 60-second
tumbling windows per service, producing latency percentiles, relevance
distributions and error rates, plus a z-score anomaly detector over a rolling
baseline of recent windows.

That is textbook stream processing, and Apache Flink is the textbook answer. But
this repository has a second requirement that pulls the other way: someone must
be able to clone it and have the whole platform running with `make dev`. A Flink
JobManager plus TaskManagers is a substantial addition to a stack that already
runs Kafka, ClickHouse, PostgreSQL, Redis, Jaeger, Prometheus and Grafana.

The state we need is also small and bounded: the current open window per service,
plus the last 30 closed windows for the baseline. That fits comfortably in
process memory and does not need a distributed state backend.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/anomaly-detection-dark.svg">
  <img alt="Anomaly detection workflow: aggregate, evaluate against a baseline, act or suppress" src="../diagrams/anomaly-detection-light.svg">
</picture>

## Decision

The metrics engine is a plain Python Kafka consumer that keeps window state in
memory and writes batched rollups to ClickHouse. It commits offsets only after a
successful insert, giving at-least-once processing with idempotent writes.

The README previously described PyFlink/PySpark. It has been corrected to
describe what is actually built.

## Consequences

### What this makes easy

- The full stack starts in about a minute on a laptop, with no JVM.
- The aggregation and detection logic is directly unit-testable: feed a list of
  events, assert on the rollup. No cluster, no test harness.
- One language across all five services, so the shared models and tracing
  bootstrap are genuinely shared.

### What this makes hard

- Parallelism is bounded by partition count and manual pod scaling. There is no
  automatic rescaling or work redistribution.
- Window state lives in memory. A pod restart loses the currently open window;
  events are reprocessed from the last committed offset, so at-least-once holds,
  but a partially aggregated window is recomputed rather than restored.
- No event-time watermarking. We window on ingest time, so a badly delayed event
  lands in the wrong window. Acceptable for operational metrics, not acceptable
  if these numbers ever become billing inputs.

### What we now have to live with

Consumer lag is the health signal for the engine, and back-pressure handling is
ours to get right rather than the framework's.

## Alternatives considered

### PyFlink

Rejected for v1, not on merit. It gives exactly-once sinks, event-time
watermarking, checkpointed state and rescaling — all of which we would want at
scale. The cost is a JobManager and TaskManagers in every environment including
the laptop one, plus a much harder path from "clone the repo" to "see it work".

**Migration path if this is revisited:** the aggregation and detection logic is
deliberately isolated in pure functions that take a batch of events and return
rollups. Replacing the consumer loop with a PyFlink job means reusing those
functions inside a `ProcessWindowFunction`; the ClickHouse sink and the schemas
stay as they are.

### PySpark Structured Streaming

Rejected for the same reason as Flink, with a worse fit: micro-batching adds
latency to a path where we want a closed window visible quickly, and Spark's
operational footprint is larger still.

### Kafka Streams

Rejected because it would mean a JVM service in an otherwise Python codebase,
losing the shared models and tracing setup.

## Revisit when

Any of these becomes true:

- Sustained ingest exceeds roughly 50,000 events per second, where manual
  partition-to-pod planning stops being reasonable.
- Late-arriving events start materially distorting windows, meaning event-time
  semantics are genuinely needed.
- These metrics acquire a consumer that requires exactly-once guarantees.
