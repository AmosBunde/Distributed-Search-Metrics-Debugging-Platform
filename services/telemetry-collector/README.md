# Telemetry Collector

The platform's ingest surface. Accepts search events over HTTP, validates and
enriches them, and publishes them to Kafka.

It deliberately does nothing else. The collector has to stay available while
everything downstream is restarting, so no aggregation, storage or analysis
happens here.

## API

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/api/v1/telemetry/event` | One event. `202` with an ingest result, `422` if invalid |
| `POST` | `/api/v1/telemetry/batch` | Up to 500 events. `202` with per-event results, `413` if larger |
| `GET` | `/health` | Liveness, plus whether Kafka and Redis are attached |
| `GET` | `/metrics` | Prometheus exposition |

```bash
curl -X POST http://localhost:8001/api/v1/telemetry/event \
  -H 'content-type: application/json' \
  -H 'x-client-id: search-api' \
  -d '{"query_id":"q-1","service":"search-api","query":"tracing","latency_ms":42.0}'
```

```json
{ "accepted": 1, "rejected": 0, "errors": [] }
```

## How it behaves

**A batch succeeds partially.** One malformed event does not reject the other
499. The response names each rejected entry and the field at fault:

```json
{ "accepted": 2, "rejected": 1, "errors": ["event 1: latency_ms: Input should be greater than or equal to 0"] }
```

**Receive time is recorded, not substituted.** The caller's `timestamp` is kept
as sent and `metadata.received_at` is added alongside it, so a client with a
skewed clock is visible rather than silently distorting a window.

**Rate limiting is a token bucket per client**, identified by the `X-Client-Id`
header or the peer address. With Redis configured the bucket is shared by every
replica and refilled atomically in a Lua script. Without Redis it falls back to
an in-process bucket, which is correct for one replica and wrong for several —
the service logs a warning saying so.

**The limiter fails open.** If Redis is unreachable the request is allowed.
Turning a cache outage into telemetry loss would be the worse failure.

**Routing is not decided here.** `SearchEvent.topic` in the shared library
decides between `search.events` and `search.errors`, so the collector cannot
disagree with the engine about where a failed query goes.

## Configuration

Everything comes from the environment (see `.env.example`). The ones that matter
here:

| Variable | Default | Effect |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Where events are published |
| `RATE_LIMIT_ENABLED` | `true` | Turns limiting off entirely when false |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `6000` | Refill rate per client |
| `RATE_LIMIT_BURST` | `600` | Bucket capacity — how big a spike is absorbed |
| `MAX_BATCH_SIZE` | `500` | Above this the request is rejected with 413 |
| `REDIS_HOST` | `redis` | Unset or unreachable ⇒ per-replica limiting |

## Running it

```bash
make dev                                    # with the rest of the stack
docker compose up telemetry-collector       # just this service
uvicorn app.main:app --port 8001 --reload   # locally, needs Kafka reachable
```

## Tests

```bash
make test-unit
```

Unit tests drive the real ASGI app with the Kafka producer and Redis replaced by
fakes: every layer the request passes through is real, but nothing needs to be
running. The Kafka and Redis connection setup itself is covered by the
integration suite.
