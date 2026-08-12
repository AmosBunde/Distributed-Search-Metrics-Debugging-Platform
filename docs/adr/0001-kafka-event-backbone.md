# 0001. Use Kafka as the event backbone

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

Search telemetry arrives continuously from many services and has several
consumers with different needs: a metrics engine that aggregates it, a debug
service that wants failures, an alerting path, and — later — anything else that
needs the same stream. Ingest volume is bursty; a traffic spike must not be lost
because one consumer is slow or restarting.

The ingest path and the analysis path therefore have to be decoupled, with a
durable buffer between them that supports replay.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/telemetry-ingest-dark.svg">
  <img alt="Telemetry ingest pipeline: sources, collector, Kafka topics, metrics engine, ClickHouse" src="../diagrams/telemetry-ingest-light.svg">
</picture>

## Decision

We publish all telemetry to Apache Kafka across four topics — `search.events`,
`search.results`, `search.errors` and `search.anomalies` — keyed by query id so
that every record for one query lands on the same partition and keeps its order.

## Consequences

### What this makes easy

- The collector's only job is to accept and validate; it is never blocked by a
  slow downstream consumer.
- Adding a consumer is free — it joins with a new consumer group and reads from
  whatever offset it wants.
- Replaying a window of history means resetting an offset, not restoring backups.
- Retention (7 days by default) gives an outage a recovery window rather than a
  data-loss event.

### What this makes hard

- Exactly-once delivery is not on the table. Consumers must be idempotent, and
  ours are: rollups are written keyed by window and service.
- Consumer lag becomes a first-class operational signal that has to be monitored
  and alerted on.
- Local development needs a broker running, which is why the `make dev` stack
  includes one.

### What we now have to live with

A managed Kafka in every cloud environment (MSK, Event Hubs, Pub/Sub), each with
its own quirks, and partition-count planning: consumer parallelism is capped by
partition count, so under-provisioning partitions caps ingest throughput.

## Alternatives considered

### Write directly to ClickHouse from the collector

Rejected. It couples ingest availability to analytics availability — a
ClickHouse restart would return errors to callers — and ClickHouse strongly
prefers few large inserts to many small ones. There would also be no way to add
a second consumer without re-reading the analytics store.

### A managed queue (SQS, Service Bus)

Rejected. Queues delete on consumption, so multiple independent consumers each
need their own copy, and there is no replay. The debug and alerting paths would
each need a separate fan-out.

### Redis Streams

Rejected. Adequate at low volume, but retention is memory-bound; a day of
telemetry is far cheaper on disk than in RAM.

## Revisit when

Sustained ingest drops below roughly a thousand events per second and only one
consumer remains — at that point the operational cost of Kafka outweighs what it
buys.
