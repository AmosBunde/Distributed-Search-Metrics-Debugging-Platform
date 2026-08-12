# API Gateway

The platform's only public surface. Metrics are read from ClickHouse behind a
Redis cache; debugging and ingest are proxied to the services that own them.

One entry point means auth, CORS, caching and rate limiting have exactly one
home — and the dashboard only ever needs one base URL.

Interactive docs: <http://localhost:8000/docs>

## Endpoints

| Method | Path | Source |
|---|---|---|
| `GET` | `/api/v1/metrics/latency` | ClickHouse — p50/p95/p99 per bucket |
| `GET` | `/api/v1/metrics/relevance` | ClickHouse — score distribution |
| `GET` | `/api/v1/metrics/errors` | ClickHouse — error rates |
| `GET` | `/api/v1/metrics/summary` | ClickHouse — overview card + per-service rows |
| `GET` | `/api/v1/anomalies` | ClickHouse — anomaly feed |
| `GET` | `/api/v1/queries/slowest` | ClickHouse — the way into debugging |
| `GET` | `/api/v1/traces/{trace_id}` | proxied to the debug service |
| `GET` | `/api/v1/debug/query/{query_id}` | proxied to the debug service |
| `POST` | `/api/v1/debug/replay` | proxied to the debug service |
| `POST` | `/api/v1/telemetry/event` | proxied to the collector |
| `POST` | `/api/v1/telemetry/batch` | proxied to the collector |

### Parameters

| Parameter | Applies to | Notes |
|---|---|---|
| `minutes` | all metrics | Lookback, 1 to 43 200 |
| `start`, `end` | time series | ISO-8601; both or neither, never one |
| `service` | all metrics | Restrict to one service |
| `interval` | time series | `1m`, `5m`, `15m`, `1h`, `1d` — anything else is rejected |
| `limit`, `offset` | anomalies, slowest | Paging, limit capped at 1000 |
| `severity` | anomalies | `info`, `warning`, `critical` |

```bash
curl "localhost:8000/api/v1/metrics/latency?minutes=60&interval=5m&service=search-api"
curl "localhost:8000/api/v1/anomalies?severity=critical&limit=20"
```

## Behaviour worth knowing

**Percentiles are averaged, and the response says so.** The raw latencies are
not in the rollup table, so a bucket's p95 is the mean of its rollups' p95s.
That is an approximation, and every latency response carries a `note` saying it.

**An unknown `interval` is rejected, not substituted.** Quietly falling back to
one minute would hand back data the caller did not ask for.

**Every value is parameterised.** Service names and severities travel as
ClickHouse query parameters, never as SQL text. The only thing that varies the
SQL is the bucket function, chosen from a fixed map.

**ClickHouse failing is a 503, not a 500.** The analytics store is upstream of
the gateway; reporting it as an internal error would point on-call at the wrong
service.

**Redis is optional.** Without it every request goes straight to ClickHouse —
slower, not broken. Cache TTLs are short and uneven: 5 s for anomalies, 10 s for
the summary card, 30 s for relevance. A stale anomaly feed is the one thing
on-call would actually mind.

**Proxy responses pass through untouched**, including status codes. A 404 from
the debug service stays a 404; an unreachable upstream is a 502.

## Configuration

| Variable | Default |
|---|---|
| `CLICKHOUSE_HOST` / `CLICKHOUSE_DB` / `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD` | `clickhouse` / `search_metrics` / `search` / — |
| `REDIS_HOST` | `redis` (unset ⇒ no caching) |
| `COLLECTOR_URL` | `http://telemetry-collector:8001` |
| `DEBUG_SERVICE_URL` | `http://debug-service:8003` |

## Tests

```bash
make test-unit
```

Queries are tested against a mock transport, asserting on the SQL and the
parameters that would have been sent — including that a hostile service name
never reaches the SQL text. A contract test asserts that every route the README
documents is actually present in the served OpenAPI schema.
