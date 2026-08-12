"""API gateway — the platform's only public surface.

Everything the dashboard and external tooling need is here: metrics read from
ClickHouse behind a Redis cache, and the debug and ingest routes proxied to the
services that own them. Keeping one entry point means auth, CORS, caching and
rate limiting have exactly one home.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from search_metrics_common import (
    configure_logging,
    configure_tracing,
    get_settings,
    instrument_fastapi,
)

from .cache import MetricsCache
from .queries import INTERVALS, MetricsQueries, QueryError, TimeRange

SERVICE_NAME = "api-gateway"

settings = get_settings()
logger = configure_logging(SERVICE_NAME, settings.log_level)

REQUESTS = Counter("gateway_requests_total", "Requests served", ["endpoint", "status"])
QUERY_DURATION = Histogram("gateway_query_duration_seconds", "Query time", ["endpoint"])
CACHE_EVENTS = Counter("gateway_cache_events_total", "Cache outcomes", ["outcome"])

# Upstreams are addressed by service name on the compose/Kubernetes network.
COLLECTOR_URL = os.environ.get("COLLECTOR_URL", "http://telemetry-collector:8001")
DEBUG_URL = os.environ.get("DEBUG_SERVICE_URL", "http://debug-service:8003")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_tracing(SERVICE_NAME, settings)

    app.state.queries = MetricsQueries(
        url=settings.clickhouse_url,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )

    redis = None
    try:
        from redis.asyncio import Redis

        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis.ping()
    except Exception:
        logger.warning("Redis unavailable: serving every request straight from ClickHouse")
        redis = None

    app.state.cache = MetricsCache(redis)
    app.state.redis = redis
    app.state.http = httpx.AsyncClient(timeout=30.0)

    try:
        yield
    finally:
        await app.state.queries.close()
        await app.state.http.aclose()
        if redis is not None:
            await redis.aclose()


app = FastAPI(
    title="Search Metrics API",
    description=(
        "Metrics, anomalies, traces and debugging for the search platform. "
        "The only public surface: everything else sits behind it."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tightened per environment in the Helm values
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


def get_queries(request: Request) -> MetricsQueries:
    return request.app.state.queries


def get_cache(request: Request) -> MetricsCache:
    return request.app.state.cache


@app.exception_handler(QueryError)
async def _query_error_handler(request: Request, exc: QueryError) -> JSONResponse:
    """The analytics store failing is a 503, not a 500: it is upstream of us."""
    logger.error("analytics query failed", extra={"path": request.url.path, "error": str(exc)})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "the analytics store could not answer", "error": str(exc)},
    )


TimeWindow = Annotated[int, Query(ge=1, le=60 * 24 * 30, description="Minutes to look back")]
ServiceFilter = Annotated[str | None, Query(max_length=64, description="Restrict to one service")]
Interval = Annotated[str, Query(description=f"Bucket size: {', '.join(sorted(INTERVALS))}")]


def _window(minutes: int, start: str | None, end: str | None) -> TimeRange:
    """Either an explicit range or a lookback, never a silent mixture."""
    if start and end:
        try:
            return TimeRange(
                start=datetime.fromisoformat(start).astimezone(UTC),
                end=datetime.fromisoformat(end).astimezone(UTC),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"start and end must be ISO-8601 timestamps: {exc}"
            ) from exc
    if start or end:
        raise HTTPException(status_code=422, detail="start and end must be given together")
    return TimeRange.last(minutes)


def _validate_interval(interval: str) -> str:
    if interval not in INTERVALS:
        raise HTTPException(
            status_code=422,
            detail=f"interval must be one of {sorted(INTERVALS)}, got {interval!r}",
        )
    return interval


@app.get("/api/v1/metrics/latency", summary="Latency percentiles by service")
async def metrics_latency(
    minutes: TimeWindow = 60,
    service: ServiceFilter = None,
    interval: Interval = "1m",
    start: str | None = None,
    end: str | None = None,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = _window(minutes, start, end)
    _validate_interval(interval)

    with QUERY_DURATION.labels(endpoint="latency").time():
        rows = await cache.get_or_set(
            "latency",
            lambda: queries.latency(window, service, interval),
            minutes=minutes, service=service, interval=interval, start=start, end=end,
        )  # fmt: skip

    REQUESTS.labels(endpoint="latency", status="200").inc()
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "interval": interval,
        "service": service,
        "series": rows,
        "note": (
            "Percentiles are averaged across rollups in each bucket; they are an "
            "approximation of the true percentile over the bucket."
        ),
    }


@app.get("/api/v1/metrics/relevance", summary="Relevance score distribution")
async def metrics_relevance(
    minutes: TimeWindow = 60,
    service: ServiceFilter = None,
    interval: Interval = "1m",
    start: str | None = None,
    end: str | None = None,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = _window(minutes, start, end)
    _validate_interval(interval)

    rows = await cache.get_or_set(
        "relevance",
        lambda: queries.relevance(window, service, interval),
        minutes=minutes, service=service, interval=interval, start=start, end=end,
    )  # fmt: skip

    REQUESTS.labels(endpoint="relevance", status="200").inc()
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "interval": interval,
        "service": service,
        "series": rows,
    }


@app.get("/api/v1/metrics/errors", summary="Error rates by service")
async def metrics_errors(
    minutes: TimeWindow = 60,
    service: ServiceFilter = None,
    interval: Interval = "1m",
    start: str | None = None,
    end: str | None = None,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = _window(minutes, start, end)
    _validate_interval(interval)

    rows = await cache.get_or_set(
        "errors",
        lambda: queries.errors(window, service, interval),
        minutes=minutes, service=service, interval=interval, start=start, end=end,
    )  # fmt: skip

    REQUESTS.labels(endpoint="errors", status="200").inc()
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "interval": interval,
        "service": service,
        "series": rows,
    }


@app.get("/api/v1/metrics/summary", summary="Dashboard overview card")
async def metrics_summary(
    minutes: TimeWindow = 60,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = TimeRange.last(minutes)

    summary = await cache.get_or_set("summary", lambda: queries.summary(window), minutes=minutes)
    services = await cache.get_or_set("services", lambda: queries.services(window), minutes=minutes)

    REQUESTS.labels(endpoint="summary", status="200").inc()
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "totals": summary,
        "services": services,
    }


@app.get("/api/v1/anomalies", summary="Detected anomalies")
async def list_anomalies(
    minutes: TimeWindow = 60 * 24,
    service: ServiceFilter = None,
    severity: Annotated[str | None, Query(pattern="^(info|warning|critical)$")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = TimeRange.last(minutes)

    rows = await cache.get_or_set(
        "anomalies",
        lambda: queries.anomalies(window, service, severity, limit, offset),
        minutes=minutes, service=service, severity=severity, limit=limit, offset=offset,
    )  # fmt: skip

    REQUESTS.labels(endpoint="anomalies", status="200").inc()
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "anomalies": rows,
    }


@app.get("/api/v1/queries/slowest", summary="Slowest queries in the window")
async def slowest_queries(
    minutes: TimeWindow = 60,
    service: ServiceFilter = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 20,
    queries: MetricsQueries = Depends(get_queries),
    cache: MetricsCache = Depends(get_cache),
) -> dict[str, Any]:
    window = TimeRange.last(minutes)

    rows = await cache.get_or_set(
        "slowest",
        lambda: queries.slowest_queries(window, service, limit),
        minutes=minutes, service=service, limit=limit,
    )  # fmt: skip

    REQUESTS.labels(endpoint="slowest", status="200").inc()
    return {"count": len(rows), "queries": rows}


# --- Proxied routes ---------------------------------------------------------
# The gateway does not reimplement debugging or ingest; it forwards to the
# service that owns them so there is still exactly one public surface.


async def _proxy(request: Request, method: str, base: str, path: str, endpoint: str) -> Response:
    """Forward a request upstream.

    `endpoint` is the route *template*, not the concrete path: labelling
    metrics with the path would give every trace id its own Prometheus series,
    which is unbounded cardinality and eventually an out-of-memory kill.
    """
    client: httpx.AsyncClient = request.app.state.http
    body = await request.body()

    try:
        upstream = await client.request(
            method,
            f"{base}{path}",
            content=body or None,
            headers={"content-type": request.headers.get("content-type", "application/json")},
            params=dict(request.query_params),
        )
    except httpx.HTTPError as exc:
        REQUESTS.labels(endpoint=endpoint, status="502").inc()
        raise HTTPException(status_code=502, detail=f"upstream service unavailable: {exc}") from exc

    REQUESTS.labels(endpoint=endpoint, status=str(upstream.status_code)).inc()
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


@app.get("/api/v1/traces/{trace_id}", summary="Full distributed trace")
async def get_trace(trace_id: str, request: Request) -> Response:
    return await _proxy(request, "GET", DEBUG_URL, f"/api/v1/traces/{trace_id}", "traces")


@app.get("/api/v1/debug/query/{query_id}", summary="Root cause debug information")
async def debug_query(query_id: str, request: Request) -> Response:
    return await _proxy(request, "GET", DEBUG_URL, f"/api/v1/debug/query/{query_id}", "debug_query")


@app.post("/api/v1/debug/replay", summary="Replay a failed query")
async def replay(request: Request) -> Response:
    return await _proxy(request, "POST", DEBUG_URL, "/api/v1/debug/replay", "replay")


@app.post("/api/v1/telemetry/event", summary="Ingest a single search event")
async def ingest_event(request: Request) -> Response:
    return await _proxy(request, "POST", COLLECTOR_URL, "/api/v1/telemetry/event", "ingest_event")


@app.post("/api/v1/telemetry/batch", summary="Ingest a batch of search events")
async def ingest_batch(request: Request) -> Response:
    return await _proxy(request, "POST", COLLECTOR_URL, "/api/v1/telemetry/batch", "ingest_batch")


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    cache: MetricsCache = request.app.state.cache
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "clickhouse": await request.app.state.queries.ping(),
        "cache": cache.enabled,
        "cache_hit_rate": round(cache.hit_rate, 3),
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


logging.getLogger(__name__).debug("gateway module loaded")
