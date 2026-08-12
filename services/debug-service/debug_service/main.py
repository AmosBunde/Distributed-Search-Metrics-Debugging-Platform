"""Debug service — traces, root cause analysis and query replay.

The half of the platform that answers "why was this query slow?" rather than
"how slow are queries in general".
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from search_metrics_common import (
    configure_logging,
    configure_tracing,
    get_settings,
    instrument_fastapi,
)

from .replay import SERVICE_NAME_PATTERN, QueryRun, ReplayJob, ReplayRequest, execute_replay
from .root_cause import analyse, slowest_service, summarise
from .storage import ClickHouseReader, ReplayJobStore
from .trace import CyclicTraceError, build_trace

SERVICE_NAME = "debug-service"

settings = get_settings()
logger = configure_logging(SERVICE_NAME, settings.log_level)

TRACES_ASSEMBLED = Counter("debug_traces_assembled_total", "Traces assembled from spans")
FINDINGS_PRODUCED = Counter("debug_findings_total", "Root cause findings", ["kind"])
REPLAYS = Counter("debug_replays_total", "Replay jobs", ["status"])
ANALYSIS_DURATION = Histogram("debug_analysis_duration_seconds", "Time to analyse one trace")


#: Services a replay may be sent to. Configured per environment; the default
#: covers the services the local stack generates traffic for.
REPLAY_TARGETS: frozenset[str] = frozenset(
    name.strip()
    for name in os.environ.get(
        "REPLAY_ALLOWED_TARGETS",
        "search-api,ranking-service,index-service,suggest-service",
    ).split(",")
    if name.strip()
)


def resolve_target(candidate: str | None, recorded_service: str | None) -> str:
    """Return a safe replay target, or refuse.

    Replay is the one place this service makes an outbound request to an
    address influenced by its caller, which makes it the one place server-side
    request forgery is possible. The target must therefore be a bare service
    name *and* be on the allowlist — a name that merely looks well-formed is not
    enough, since the internal network contains plenty of things that should
    never be reachable this way.
    """
    target = candidate or recorded_service
    if not target:
        raise ValueError("no replay target: none requested and none recorded")

    if not re.match(SERVICE_NAME_PATTERN, target):
        raise ValueError(f"{target!r} is not a service name")

    if target not in REPLAY_TARGETS:
        raise ValueError(
            f"{target!r} is not an allowed replay target "
            f"(allowed: {', '.join(sorted(REPLAY_TARGETS))})"
        )

    return target


class HttpReplayExecutor:
    """Re-issues a query against the target service over HTTP."""

    def __init__(self, client: Any = None, timeout: float = 30.0) -> None:
        self._client = client
        self._timeout = timeout

    async def run(self, query: str, target_service: str) -> QueryRun:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        response = await client.post(
            f"http://{target_service}/search", json={"query": query}, timeout=self._timeout
        )
        response.raise_for_status()
        body = response.json()

        return QueryRun(
            query=query,
            latency_ms=float(body.get("latency_ms", 0.0)),
            result_count=int(body.get("result_count", len(body.get("results", [])))),
            status=str(body.get("status", "ok")),
            document_ids=[str(doc) for doc in body.get("document_ids", [])],
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_tracing(SERVICE_NAME, settings)

    app.state.reader = ClickHouseReader(
        url=settings.clickhouse_url,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )

    pool = None
    try:
        import asyncpg

        pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    except Exception:
        logger.warning("PostgreSQL unavailable: replay jobs will not be persisted")

    app.state.pool = pool
    app.state.jobs = ReplayJobStore(pool)
    app.state.executor = HttpReplayExecutor()

    try:
        yield
    finally:
        await app.state.reader.close()
        if pool is not None:
            await pool.close()


app = FastAPI(
    title="Debug Service",
    description="Distributed traces, root cause analysis and query replay.",
    version="0.1.0",
    lifespan=lifespan,
)
instrument_fastapi(app)


def get_reader(request: Request) -> ClickHouseReader:
    return request.app.state.reader


def get_jobs(request: Request) -> ReplayJobStore:
    return request.app.state.jobs


@app.get("/api/v1/traces/{trace_id}", summary="Assemble a distributed trace")
async def get_trace(trace_id: str, reader: ClickHouseReader = Depends(get_reader)) -> dict:
    spans = await reader.spans_for_trace(trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"no spans found for trace {trace_id}")

    try:
        tree = build_trace(spans)
    except CyclicTraceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    TRACES_ASSEMBLED.inc()
    payload = tree.as_dict()
    payload["critical_path"] = [
        {
            "service": node.span.service,
            "operation": node.span.operation,
            "duration_ms": node.span.duration_ms,
        }
        for node in tree.critical_path()
    ]
    return payload


@app.get("/api/v1/debug/query/{query_id}", summary="Root cause analysis for one query")
async def debug_query(query_id: str, reader: ClickHouseReader = Depends(get_reader)) -> dict:
    event = await reader.event_for_query(query_id)
    spans = await reader.spans_for_query(query_id)

    if event is None and not spans:
        raise HTTPException(status_code=404, detail=f"nothing recorded for query {query_id}")

    with ANALYSIS_DURATION.time():
        tree = build_trace(spans)
        baselines = await reader.service_baselines()
        findings = analyse(tree, baselines)

    for finding in findings:
        FINDINGS_PRODUCED.labels(kind=str(finding.kind)).inc()

    hotspot = slowest_service(tree)
    return {
        "query_id": query_id,
        "event": event,
        "summary": summarise(findings),
        "findings": [finding.as_dict() for finding in findings],
        "slowest_service": {"service": hotspot[0], "self_time_ms": hotspot[1]} if hotspot else None,
        "trace": tree.as_dict(),
        "baselines": baselines,
    }


@app.post(
    "/api/v1/debug/replay",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a recorded query",
)
async def replay_query(
    request: ReplayRequest,
    http_request: Request,
    reader: ClickHouseReader = Depends(get_reader),
    jobs: ReplayJobStore = Depends(get_jobs),
) -> dict:
    original = await reader.original_run(request.query_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"no recorded run for query {request.query_id}")

    event = await reader.event_for_query(request.query_id)
    try:
        target = resolve_target(request.target_service, (event or {}).get("service"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = ReplayJob(
        id=uuid.uuid4(),
        query_id=request.query_id,
        target_service=target,
        requested_by=request.requested_by,
    )
    await jobs.save(job)

    job = await execute_replay(job, original, http_request.app.state.executor)
    await jobs.save(job)

    REPLAYS.labels(status=str(job.status)).inc()
    return job.as_dict()


@app.get("/api/v1/debug/replay/{job_id}", summary="Fetch a replay job")
async def get_replay(job_id: str, jobs: ReplayJobStore = Depends(get_jobs)) -> dict:
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no replay job {job_id}")
    return job


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "clickhouse": getattr(request.app.state, "reader", None) is not None,
        "postgres": getattr(request.app.state, "pool", None) is not None,
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


logging.getLogger(__name__).debug("debug service module loaded")
