"""Telemetry collector — the platform's ingest surface.

Accepts search events over HTTP, validates and enriches them, and publishes them
to Kafka. It deliberately does nothing else: the collector must stay available
under load, so no aggregation, storage or analysis happens here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from search_metrics_common import (
    EventPublisher,
    IngestResult,
    SearchEvent,
    build_producer,
    configure_logging,
    configure_tracing,
    get_settings,
    instrument_fastapi,
)

from .ingest import ingest_events, ingest_spans
from .rate_limit import RateLimitDecision, build_rate_limiter

SERVICE_NAME = "telemetry-collector"

settings = get_settings()
logger = configure_logging(SERVICE_NAME, settings.log_level)

EVENTS_INGESTED = Counter(
    "collector_events_ingested_total", "Events accepted and published", ["status"]
)
INGEST_LATENCY = Histogram(
    "collector_ingest_duration_seconds", "Time to validate and publish a request", ["endpoint"]
)
RATE_LIMITED = Counter("collector_rate_limited_total", "Requests rejected by the rate limiter")
SPANS_INGESTED = Counter("collector_spans_ingested_total", "Trace spans accepted")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the Kafka producer and Redis connection for the process lifetime."""
    configure_tracing(SERVICE_NAME, settings)

    producer = build_producer(settings)
    await producer.start()
    app.state.publisher = EventPublisher(producer, settings)

    redis = None
    if settings.rate_limit_enabled:
        try:
            from redis.asyncio import Redis

            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            await redis.ping()
        except Exception:
            logger.warning("Redis unavailable, falling back to per-replica rate limiting")
            redis = None

    app.state.redis = redis
    app.state.rate_limiter = build_rate_limiter(settings, redis)
    logger.info("collector ready", extra={"brokers": settings.bootstrap_servers})

    try:
        yield
    finally:
        await producer.stop()
        if redis is not None:
            await redis.aclose()


app = FastAPI(
    title="Telemetry Collector",
    description="Ingest surface for search telemetry.",
    version="0.1.0",
    lifespan=lifespan,
)
instrument_fastapi(app)


def get_publisher(request: Request) -> Any:
    return request.app.state.publisher


async def enforce_rate_limit(
    request: Request,
    x_client_id: str | None = Header(default=None),
) -> None:
    """Apply the per-client limit, or fall back to the peer address."""
    limiter = request.app.state.rate_limiter
    client = x_client_id or (request.client.host if request.client else "anonymous")
    decision: RateLimitDecision = await limiter.check(client)

    if not decision.allowed:
        RATE_LIMITED.inc()
        raise RateLimitExceeded(decision.retry_after_seconds)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("rate limit exceeded")


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "rate limit exceeded", "retry_after": exc.retry_after_seconds},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


@app.post(
    "/api/v1/telemetry/event",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResult,
    summary="Ingest a single search event",
    dependencies=[Depends(enforce_rate_limit)],
)
async def ingest_event(event: SearchEvent, publisher=Depends(get_publisher)) -> IngestResult:
    with INGEST_LATENCY.labels(endpoint="event").time():
        result = await ingest_events([event.model_dump()], publisher)
    EVENTS_INGESTED.labels(status="accepted").inc(result.accepted)
    return result


@app.post(
    "/api/v1/telemetry/batch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResult,
    summary="Ingest a batch of search events",
    dependencies=[Depends(enforce_rate_limit)],
)
async def ingest_batch(payload: dict, publisher=Depends(get_publisher)) -> IngestResult:
    """Accept a batch, reporting per-event failures rather than rejecting it whole.

    The body is taken untyped on purpose: validating each event individually is
    what makes partial success possible.
    """
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise BadBatch("body must contain a non-empty 'events' array")
    if len(events) > settings.max_batch_size:
        raise BatchTooLarge(len(events), settings.max_batch_size)

    with INGEST_LATENCY.labels(endpoint="batch").time():
        result = await ingest_events(events, publisher)

    EVENTS_INGESTED.labels(status="accepted").inc(result.accepted)
    EVENTS_INGESTED.labels(status="rejected").inc(result.rejected)
    return result


@app.post(
    "/api/v1/telemetry/spans",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResult,
    summary="Ingest trace spans",
    dependencies=[Depends(enforce_rate_limit)],
)
async def ingest_span_batch(payload: dict, publisher=Depends(get_publisher)) -> IngestResult:
    """Accept spans for the trace store.

    Spans arrive the same way events do so an adopter has one ingest path to
    point services at. A deployment already running an OpenTelemetry collector
    can write the same records from there instead.
    """
    spans = payload.get("spans")
    if not isinstance(spans, list) or not spans:
        raise BadBatch("body must contain a non-empty 'spans' array")
    if len(spans) > settings.max_batch_size:
        raise BatchTooLarge(len(spans), settings.max_batch_size)

    with INGEST_LATENCY.labels(endpoint="spans").time():
        result = await ingest_spans(spans, publisher)

    SPANS_INGESTED.inc(result.accepted)
    return result


class BadBatch(Exception):
    pass


class BatchTooLarge(Exception):
    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__("batch too large")


@app.exception_handler(BadBatch)
async def _bad_batch_handler(request: Request, exc: BadBatch) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


@app.exception_handler(BatchTooLarge)
async def _batch_too_large_handler(request: Request, exc: BatchTooLarge) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        content={
            "detail": f"batch of {exc.size} exceeds the limit of {exc.limit} events",
            "limit": exc.limit,
        },
    )


@app.get("/health", summary="Liveness and readiness")
async def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "kafka": bool(getattr(request.app.state, "publisher", None)),
        "redis": getattr(request.app.state, "redis", None) is not None,
    }


@app.get("/metrics", summary="Prometheus metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


logging.getLogger(__name__).debug("collector module loaded")
