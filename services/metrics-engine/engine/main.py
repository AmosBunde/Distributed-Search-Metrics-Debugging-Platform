"""Metrics engine — consumes the event stream and produces metrics.

A plain Kafka consumer rather than a stream processing framework; ADR-0003
explains why, and what would make that decision worth revisiting.

The consumer loop is the only place that commits offsets, and it commits only
after ClickHouse has accepted the batch. That ordering is what makes
at-least-once delivery hold: a crash between insert and commit replays the
batch, and `ReplacingMergeTree` collapses the duplicate rollups.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from search_metrics_common import (
    EventPublisher,
    Topic,
    build_consumer,
    build_producer,
    configure_logging,
    configure_tracing,
    get_settings,
)
from search_metrics_common.kafka import deserialize

from .aggregation import WindowedAggregator
from .anomaly import AnomalyDetector
from .clickhouse import ClickHouseWriter
from .pipeline import Pipeline, anomaly_row, event_row

SERVICE_NAME = "metrics-engine"

settings = get_settings()
logger = configure_logging(SERVICE_NAME, settings.log_level)

EVENTS_PROCESSED = Counter("engine_events_processed_total", "Events consumed and aggregated")
EVENTS_INVALID = Counter("engine_events_invalid_total", "Records that could not be decoded")
ROLLUPS_WRITTEN = Counter("engine_rollups_written_total", "Window rollups written to ClickHouse")
ANOMALIES_DETECTED = Counter("engine_anomalies_detected_total", "Anomalies published", ["severity"])
OPEN_WINDOWS = Gauge("engine_open_windows", "Windows currently accumulating")
FLUSH_DURATION = Histogram("engine_flush_duration_seconds", "Time spent writing to ClickHouse")


class Engine:
    """Owns the consumer loop and the resources it needs."""

    def __init__(self) -> None:
        self.pipeline = Pipeline(
            WindowedAggregator(window_seconds=settings.window_seconds),
            AnomalyDetector(
                threshold=settings.anomaly_zscore_threshold,
                baseline_windows=settings.anomaly_baseline_windows,
            ),
        )
        self.writer = ClickHouseWriter(
            url=settings.clickhouse_url,
            database=settings.clickhouse_db,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
            batch_size=settings.clickhouse_insert_batch_size,
            flush_interval_seconds=settings.clickhouse_insert_interval_seconds,
        )
        self.consumer = None
        self.producer = None
        self.publisher: EventPublisher | None = None
        self.running = False
        self.healthy = False

    async def start(self) -> None:
        topics = [
            settings.topic_name(Topic.EVENTS),
            settings.topic_name(Topic.ERRORS),
        ]
        self.consumer = build_consumer(settings, topics, group_id=settings.kafka_consumer_group)
        self.producer = build_producer(settings)

        await self.consumer.start()
        await self.producer.start()
        self.publisher = EventPublisher(self.producer, settings)
        self.running = True
        self.healthy = True
        logger.info("engine started", extra={"topics": topics})

    async def stop(self) -> None:
        self.running = False
        # Close open windows so a restart does not silently lose the partial
        # window that was in flight.
        outcome = self.pipeline.flush_windows()
        if outcome.rollups:
            self.writer.add("metric_rollups", [r.as_row() for r in outcome.rollups])
            with contextlib.suppress(Exception):
                await self.writer.flush()

        if self.consumer is not None:
            await self.consumer.stop()
        if self.producer is not None:
            await self.producer.stop()
        await self.writer.close()
        self.healthy = False

    async def run(self) -> None:
        """Poll, process, write, then commit — in that order, always."""
        assert self.consumer is not None

        while self.running:
            try:
                batches = await self.consumer.getmany(
                    timeout_ms=int(settings.clickhouse_insert_interval_seconds * 1000),
                    max_records=settings.clickhouse_insert_batch_size,
                )
                payloads = [
                    deserialize(record.value) for records in batches.values() for record in records
                ]

                outcome = self.pipeline.process_batch(payloads)
                EVENTS_PROCESSED.inc(outcome.processed)
                EVENTS_INVALID.inc(outcome.invalid)
                OPEN_WINDOWS.set(self.pipeline.open_windows)

                self.writer.add("events", [event_row(event) for event in outcome.events])
                self.writer.add("metric_rollups", [rollup.as_row() for rollup in outcome.rollups])
                self.writer.add(
                    "anomalies", [anomaly_row(anomaly) for anomaly in outcome.anomalies]
                )

                if self.writer.should_flush or outcome.rollups:
                    with FLUSH_DURATION.time():
                        await self.writer.flush()
                    ROLLUPS_WRITTEN.inc(len(outcome.rollups))

                for anomaly in outcome.anomalies:
                    ANOMALIES_DETECTED.labels(severity=str(anomaly.severity)).inc()
                    if self.publisher is not None:
                        await self.publisher.publish_anomaly(anomaly)
                    logger.warning(
                        "anomaly detected",
                        extra={
                            "service": anomaly.service,
                            "metric": anomaly.metric,
                            "z_score": round(anomaly.z_score, 2),
                            "observed": anomaly.observed,
                        },
                    )

                # Only now is the work durable, so only now may offsets move.
                if payloads:
                    await self.consumer.commit()

            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not commit: the batch will be redelivered.
                self.healthy = False
                logger.exception("batch failed, will retry from the last committed offset")
                await asyncio.sleep(2)
                self.healthy = True


engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_tracing(SERVICE_NAME, settings)
    await engine.start()
    task = asyncio.create_task(engine.run())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await engine.stop()


app = FastAPI(title="Metrics Engine", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if engine.healthy else "degraded",
        "service": SERVICE_NAME,
        "open_windows": engine.pipeline.open_windows,
        "buffered_rows": engine.writer.buffered(),
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


logging.getLogger(__name__).debug("engine module loaded")
