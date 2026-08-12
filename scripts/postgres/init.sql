-- PostgreSQL holds the platform's mutable metadata.
--
-- Everything here needs transactions, updates or uniqueness — which is exactly
-- what ClickHouse is bad at (ADR-0002). Anything append-only and analytical
-- belongs there instead.

CREATE TABLE IF NOT EXISTS services (
    name          TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    team          TEXT,
    slo_p99_ms    INTEGER CHECK (slo_p99_ms > 0),
    slo_error_rate NUMERIC(5, 4) CHECK (slo_error_rate BETWEEN 0 AND 1),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ
);

COMMENT ON TABLE services IS
    'Known services and their SLO targets. Populated on first telemetry received.';

-- Replay jobs ---------------------------------------------------------------
-- A replay moves through states and is updated in place, so it lives here.
CREATE TABLE IF NOT EXISTS replay_jobs (
    id              UUID PRIMARY KEY,
    query_id        TEXT NOT NULL,
    trace_id        TEXT,
    target_service  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    requested_by    TEXT,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    original_latency_ms  DOUBLE PRECISION,
    replay_latency_ms    DOUBLE PRECISION,
    original_result_count INTEGER,
    replay_result_count   INTEGER,
    results_match   BOOLEAN,
    diff            JSONB,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS replay_jobs_query_id_idx ON replay_jobs (query_id);
CREATE INDEX IF NOT EXISTS replay_jobs_status_idx ON replay_jobs (status, requested_at DESC);

COMMENT ON TABLE replay_jobs IS
    'One row per replay request, updated in place as it runs.';

-- Alert state ---------------------------------------------------------------
-- Deduplication needs a uniqueness constraint: an anomaly that is already
-- firing must not page a second time (see the anomaly-detection diagram).
CREATE TABLE IF NOT EXISTS alert_state (
    signature     TEXT PRIMARY KEY,
    service       TEXT NOT NULL,
    metric        TEXT NOT NULL,
    severity      TEXT NOT NULL,
    first_fired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fired_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    fire_count    INTEGER NOT NULL DEFAULT 1,
    resolved_at   TIMESTAMPTZ,
    notified_channels TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS alert_state_active_idx
    ON alert_state (service, metric) WHERE resolved_at IS NULL;

COMMENT ON TABLE alert_state IS
    'Deduplication and lifecycle for anomaly alerts, keyed by anomaly signature.';

-- Seed the services the local simulator generates traffic for, so the stack has
-- SLO targets to compare against from the first run.
INSERT INTO services (name, display_name, team, slo_p99_ms, slo_error_rate)
VALUES
    ('search-api',      'Search API',          'search-platform', 2000, 0.0100),
    ('ranking-service', 'Ranking Service',     'relevance',       1500, 0.0050),
    ('index-service',   'Index Service',       'search-platform', 3000, 0.0100),
    ('suggest-service', 'Suggestion Service',  'search-platform',  500, 0.0200)
ON CONFLICT (name) DO NOTHING;
