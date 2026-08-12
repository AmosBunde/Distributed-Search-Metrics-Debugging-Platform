# search-metrics-common

Everything that crosses a service boundary, defined once.

```bash
pip install -e libs/common
```

| Module | What it owns |
|---|---|
| `models` | The event contract: `SearchEvent`, `SearchResult`, `AnomalyEvent`, batch and ingest-result shapes |
| `settings` | Configuration from the environment, mirroring `.env.example` field for field |
| `logging` | JSON log records carrying the active trace and span id |
| `tracing` | OpenTelemetry bootstrap, plus trace-context propagation across Kafka |
| `kafka` | Producer/consumer defaults and `EventPublisher` |
| `topics` | Logical topic names and the routing rule |

## The parts worth knowing

**Routing lives on the model.** `SearchEvent.topic` decides between the events
and errors topics, and `SearchEvent.partition_key` is always the query id. No
service invents its own rule, so they cannot disagree.

**Validation is strict on purpose.** A negative latency, a relevance score
outside `0..1`, a naive timestamp or an unknown field is rejected at ingest.
Each of those would otherwise produce a quietly wrong metric rather than an
obvious failure.

**Trace context crosses Kafka manually.** HTTP propagation is automatic once a
service is instrumented; Kafka is not. `inject_trace_context()` and
`extract_trace_context()` are what keep a trace intact from collector to engine.

**Offsets are committed by the caller.** `build_consumer` disables auto-commit,
because at-least-once only holds if the offset moves *after* the work is durable.

## Usage

```python
from search_metrics_common import (
    EventPublisher, SearchEvent, configure_logging, configure_tracing,
    get_settings, producer_context,
)

settings = get_settings()
log = configure_logging("telemetry-collector", settings.log_level)
tracer = configure_tracing("telemetry-collector", settings)

async with producer_context(settings) as producer:
    publisher = EventPublisher(producer, settings)
    await publisher.publish_event(
        SearchEvent(query_id="q-1", service="search-api", query="tracing", latency_ms=42.0)
    )
```

## Testing against it

`EventPublisher` wraps a producer rather than creating one, so a test passes a
fake object with a `send_and_wait` coroutine and asserts on what would have been
sent — no broker required. `tests/unit/test_common_infrastructure.py` shows the
pattern.
