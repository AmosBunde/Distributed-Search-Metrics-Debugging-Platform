-- ClickHouse schema for the search metrics platform.
--
-- Applied automatically on first start of the container. Three shapes of table:
--   events          raw telemetry, the source of truth for debugging
--   metric_rollups  windowed aggregates, what the dashboard actually queries
--   anomalies       detected z-score breaches
--
-- The rollup and anomaly tables use ReplacingMergeTree keyed by the window, so
-- reprocessing a window after a consumer restart overwrites rather than
-- double-counts. That is what makes the engine's at-least-once delivery safe
-- (ADR-0003).

CREATE DATABASE IF NOT EXISTS search_metrics;

-- Raw events ----------------------------------------------------------------
-- Ordered by (service, timestamp) because every dashboard query filters on a
-- service and a time range; query_id last so a single-query lookup is still a
-- narrow scan.
CREATE TABLE IF NOT EXISTS search_metrics.events
(
    event_id        UUID,
    query_id        String,
    trace_id        String DEFAULT '',
    span_id         String DEFAULT '',
    service         LowCardinality(String),
    query           String,
    index_name      LowCardinality(String) DEFAULT '',
    timestamp       DateTime64(3, 'UTC'),
    received_at     DateTime64(3, 'UTC'),
    latency_ms      Float64,
    status          LowCardinality(String),
    result_count    UInt32,
    relevance_score Nullable(Float64),
    cache_hit       UInt8 DEFAULT 0,
    user_id         String DEFAULT '',
    session_id      String DEFAULT '',
    error_type      LowCardinality(String) DEFAULT '',
    error_message   String DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (service, timestamp, query_id)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

-- Per-document results ------------------------------------------------------
-- Kept shorter than the events themselves: useful for relevance debugging,
-- expensive to retain, and not needed for historical metrics.
CREATE TABLE IF NOT EXISTS search_metrics.query_results
(
    query_id    String,
    service     LowCardinality(String),
    timestamp   DateTime64(3, 'UTC'),
    document_id String,
    rank        UInt16,
    score       Float64,
    title       String DEFAULT ''
)
ENGINE = MergeTree
PARTITION BY toDate(timestamp)
ORDER BY (query_id, rank)
TTL toDateTime(timestamp) + INTERVAL 30 DAY;

-- Windowed rollups ----------------------------------------------------------
-- One row per (service, window). Re-inserting a window replaces it.
CREATE TABLE IF NOT EXISTS search_metrics.metric_rollups
(
    window_start   DateTime('UTC'),
    window_end     DateTime('UTC'),
    service        LowCardinality(String),
    query_count    UInt64,
    error_count    UInt64,
    error_rate     Float64,
    latency_p50    Float64,
    latency_p95    Float64,
    latency_p99    Float64,
    latency_avg    Float64,
    latency_max    Float64,
    relevance_avg  Nullable(Float64),
    relevance_p10  Nullable(Float64),
    cache_hit_rate Float64,
    inserted_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toDate(window_start)
ORDER BY (service, window_start)
TTL window_start + INTERVAL 365 DAY;

-- Anomalies -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_metrics.anomalies
(
    anomaly_id      UUID,
    service         LowCardinality(String),
    metric          LowCardinality(String),
    window_start    DateTime('UTC'),
    window_end      DateTime('UTC'),
    observed        Float64,
    baseline_mean   Float64,
    baseline_stddev Float64,
    z_score         Float64,
    severity        LowCardinality(String),
    sample_count    UInt32,
    detected_at     DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(detected_at)
PARTITION BY toDate(window_start)
ORDER BY (service, metric, window_start)
TTL toDateTime(window_start) + INTERVAL 365 DAY;

-- Spans ---------------------------------------------------------------------
-- The debug service assembles trace waterfalls from here. Jaeger holds the same
-- data for browsing; this copy is what root cause analysis queries alongside
-- metrics without a second backend.
CREATE TABLE IF NOT EXISTS search_metrics.spans
(
    trace_id       String,
    span_id        String,
    parent_span_id String DEFAULT '',
    query_id       String DEFAULT '',
    service        LowCardinality(String),
    operation      LowCardinality(String),
    start_time     DateTime64(6, 'UTC'),
    duration_ms    Float64,
    status         LowCardinality(String) DEFAULT 'ok',
    attributes     Map(String, String)
)
ENGINE = MergeTree
PARTITION BY toDate(start_time)
ORDER BY (trace_id, start_time)
TTL toDateTime(start_time) + INTERVAL 7 DAY;
