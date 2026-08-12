# Metrics Engine

Consumes `search.events` and `search.errors`, aggregates them into per-service
time windows, writes rollups to ClickHouse, and detects anomalies against a
rolling baseline.

A plain Python Kafka consumer, not a stream processing framework —
[ADR-0003](../../docs/adr/0003-python-consumer-instead-of-flink.md) explains why
and what would make that worth revisiting.

## What it computes

Per service, per 60-second window:

| Metric | Notes |
|---|---|
| `latency_p50/p95/p99` | Nearest-rank, so every reported value actually occurred |
| `latency_avg`, `latency_max` | |
| `error_rate` | Every non-`ok` status counts |
| `relevance_avg`, `relevance_p10` | `NULL` when no event reported a score — absent is not zero |
| `cache_hit_rate` | |
| `query_count` | Also the anomaly detector's volume signal |

## How it behaves

**Windows are epoch-aligned and tumbling.** Every replica derives the same
boundaries without coordinating.

**Events land in the window their timestamp claims**, not the one they arrived
in. A late event joins its own window; if that window was already emitted, the
rollup is recomputed and *replaces* the old row — `metric_rollups` is a
`ReplacingMergeTree` keyed by `(service, window_start)`.

**A window closes when the watermark passes it.** The watermark is the latest
event timestamp seen, less a five-second grace period. Grace exists so a window
is not emitted while stragglers are still arriving.

**Offsets are committed last.** Poll → aggregate → write to ClickHouse → commit.
A crash between the write and the commit replays the batch, and the replacing
merge tree collapses the duplicates. Committing earlier would lose data.

**The buffer flushes on size or age.** ClickHouse wants few large inserts, but
under light traffic a handful of raw events must not sit unwritten — those are
exactly what someone debugging one slow query is looking for.

**On shutdown, open windows are flushed** so the partial window in flight is not
silently lost.

## Anomaly detection

A window is scored against that service's own last 30 windows, per metric
(`latency_p95`, `latency_p99`, `error_rate`, `query_count`). Nothing is reported
unless:

- the baseline has at least 5 windows — a new service must not alert on its
  first wobble;
- the baseline has non-zero variance — a perfectly flat series makes every
  z-score infinite, which is not a signal;
- the window has at least 10 queries — three queries say nothing about a
  service, however extreme they look.

Beyond `|z| ≥ 3` an `AnomalyEvent` is published to `search.anomalies` and
written to ClickHouse. `|z| ≥ 6` is `critical` rather than `warning`. Drops are
anomalies too: traffic vanishing matters as much as latency exploding.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `WINDOW_SECONDS` | `60` | Window length |
| `ANOMALY_ZSCORE_THRESHOLD` | `3.0` | Standard deviations before reporting |
| `ANOMALY_BASELINE_WINDOWS` | `30` | How much history the baseline keeps |
| `CLICKHOUSE_INSERT_BATCH_SIZE` | `1000` | Rows before a flush |
| `CLICKHOUSE_INSERT_INTERVAL_SECONDS` | `5` | Age before a partial batch flushes |
| `KAFKA_CONSUMER_GROUP` | `metrics-engine` | Consumer group |

## Operating it

```bash
curl localhost:8002/health     # open windows and buffered rows
make check-kafka               # consumer lag — the back-pressure signal
make check-metrics             # are rows landing in ClickHouse?
```

Rising consumer lag means the engine cannot keep up with ingest. Parallelism is
bounded by partition count, so the lever is more partitions and more replicas.

## Tests

```bash
make test-unit
```

The aggregation and detection logic is pure, so it is tested by handing it
events and comparing against numbers worked out by hand — no infrastructure. The
ClickHouse writer is tested against a mock transport, including that a permanent
failure leaves rows buffered so offsets are not committed.
