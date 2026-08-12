"""Kafka producer and consumer helpers with the platform's defaults baked in.

The defaults are not arbitrary and should not be overridden casually:

* **lz4 compression** — telemetry is highly repetitive JSON and compresses to a
  fraction of its size, which matters most on the cross-AZ hop.
* **idempotent producer with acks=all** — a retry must not create a duplicate
  record, and a record acknowledged before replication is a record that can
  vanish.
* **manual offset commits** — the metrics engine commits only after ClickHouse
  has accepted the insert, which is what makes at-least-once actually hold
  (ADR-0003).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .models import AnomalyEvent, SearchEvent
from .settings import Settings
from .topics import Topic
from .tracing import inject_trace_context


def serialize(payload: Any) -> bytes:
    """Encode a model or mapping as UTF-8 JSON."""
    if hasattr(payload, "model_dump_json"):
        return payload.model_dump_json().encode("utf-8")
    return json.dumps(payload, default=str).encode("utf-8")


def deserialize(raw: bytes) -> dict[str, Any]:
    return json.loads(raw.decode("utf-8"))


def build_producer(settings: Settings, **overrides: Any) -> AIOKafkaProducer:
    options: dict[str, Any] = {
        "bootstrap_servers": settings.bootstrap_servers,
        "compression_type": "lz4",
        "enable_idempotence": True,
        "acks": "all",
        "linger_ms": 20,
        "max_batch_size": 64 * 1024,
        "request_timeout_ms": 30_000,
    }
    options.update(overrides)
    return AIOKafkaProducer(**options)


def build_consumer(
    settings: Settings,
    topics: list[str],
    group_id: str | None = None,
    **overrides: Any,
) -> AIOKafkaConsumer:
    options: dict[str, Any] = {
        "bootstrap_servers": settings.bootstrap_servers,
        "group_id": group_id or settings.kafka_consumer_group,
        # Offsets are committed by the caller once the work is durable; see the
        # module docstring.
        "enable_auto_commit": False,
        "auto_offset_reset": "earliest",
        "max_poll_records": 500,
        "session_timeout_ms": 30_000,
    }
    options.update(overrides)
    return AIOKafkaConsumer(*topics, **options)


@asynccontextmanager
async def producer_context(settings: Settings, **overrides: Any) -> AsyncIterator[AIOKafkaProducer]:
    """Start a producer, and always flush and stop it on the way out."""
    producer = build_producer(settings, **overrides)
    await producer.start()
    try:
        yield producer
    finally:
        await producer.stop()


class EventPublisher:
    """Publishes platform events to their topic, with trace context attached.

    Wraps a producer rather than owning one, so tests can pass a fake and assert
    on what would have been sent without a broker.
    """

    def __init__(self, producer: Any, settings: Settings) -> None:
        self._producer = producer
        self._settings = settings

    async def publish(self, topic: Topic, key: str, payload: Any) -> None:
        await self._producer.send_and_wait(
            self._settings.topic_name(topic),
            value=serialize(payload),
            key=key.encode("utf-8"),
            headers=inject_trace_context(),
        )

    async def publish_event(self, event: SearchEvent) -> None:
        """Route by status: failures to the errors topic, successes to events."""
        await self.publish(event.topic, event.partition_key, event)

    async def publish_results(self, event: SearchEvent) -> None:
        """Publish the per-document results of a query, if it carried any."""
        if not event.results:
            return
        await self.publish(
            Topic.RESULTS,
            event.partition_key,
            {
                "query_id": event.query_id,
                "service": event.service,
                "timestamp": event.timestamp,
                "results": [result.model_dump() for result in event.results],
            },
        )

    async def publish_anomaly(self, anomaly: AnomalyEvent) -> None:
        await self.publish(Topic.ANOMALIES, anomaly.service, anomaly)
