"""Reading spans and query history from ClickHouse, and replay jobs from Postgres.

Every query is parameterised. ClickHouse's HTTP interface takes parameters as
`param_<name>` and refers to them as `{name:Type}` in the SQL, which keeps
identifiers and values apart even though this is a read-only path.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .replay import QueryRun
from .trace import Span

logger = logging.getLogger(__name__)


class ClickHouseReader:
    """Read-only access to spans, events and baselines."""

    def __init__(
        self,
        url: str,
        database: str,
        user: str,
        password: str,
        client: Any = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._auth = (user, password)
        self._client = client

    async def _http(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        client = await self._http()
        request_params = {"database": self._database, "default_format": "JSONEachRow"}
        for name, value in (params or {}).items():
            request_params[f"param_{name}"] = str(value)

        response = await client.post(
            self._url, params=request_params, content=sql.encode("utf-8"), auth=self._auth
        )
        response.raise_for_status()
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    async def spans_for_trace(self, trace_id: str) -> list[Span]:
        rows = await self.query(
            """
            SELECT trace_id, span_id, parent_span_id, query_id, service, operation,
                   toString(start_time) AS start_time, duration_ms, status, attributes
            FROM spans
            WHERE trace_id = {trace_id:String}
            ORDER BY start_time
            """,
            {"trace_id": trace_id},
        )
        return [Span.model_validate(row) for row in rows]

    async def spans_for_query(self, query_id: str) -> list[Span]:
        rows = await self.query(
            """
            SELECT trace_id, span_id, parent_span_id, query_id, service, operation,
                   toString(start_time) AS start_time, duration_ms, status, attributes
            FROM spans
            WHERE query_id = {query_id:String}
            ORDER BY start_time
            """,
            {"query_id": query_id},
        )
        return [Span.model_validate(row) for row in rows]

    async def event_for_query(self, query_id: str) -> dict[str, Any] | None:
        rows = await self.query(
            """
            SELECT query_id, trace_id, service, query, latency_ms, status,
                   result_count, relevance_score, cache_hit, error_type, error_message,
                   toString(timestamp) AS timestamp
            FROM events
            WHERE query_id = {query_id:String}
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"query_id": query_id},
        )
        return rows[0] if rows else None

    async def documents_for_query(self, query_id: str) -> list[str]:
        rows = await self.query(
            """
            SELECT document_id
            FROM query_results
            WHERE query_id = {query_id:String}
            ORDER BY rank
            """,
            {"query_id": query_id},
        )
        return [row["document_id"] for row in rows]

    async def service_baselines(self, lookback_minutes: int = 60) -> dict[str, float]:
        """Recent p95 per service — what "slow" is measured against."""
        rows = await self.query(
            """
            SELECT service, avg(latency_p95) AS baseline
            FROM metric_rollups
            WHERE window_start > now() - INTERVAL {minutes:UInt32} MINUTE
            GROUP BY service
            """,
            {"minutes": lookback_minutes},
        )
        return {row["service"]: float(row["baseline"]) for row in rows if row["baseline"]}

    async def original_run(self, query_id: str) -> QueryRun | None:
        """Reconstruct the recorded execution that a replay is compared against."""
        event = await self.event_for_query(query_id)
        if event is None:
            return None

        return QueryRun(
            query=event["query"],
            latency_ms=float(event["latency_ms"]),
            result_count=int(event["result_count"]),
            status=str(event["status"]),
            document_ids=await self.documents_for_query(query_id),
        )

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()


class ReplayJobStore:
    """Persists replay jobs in PostgreSQL, which is where mutable state lives."""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool
        self._memory: dict[str, dict[str, Any]] = {}

    async def save(self, job: Any) -> None:
        record = job.as_dict()
        if self._pool is None:
            # No database configured (tests, or a degraded start): keep the job
            # in memory so the API still answers, and say so in /health.
            self._memory[record["id"]] = record
            return

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO replay_jobs (
                    id, query_id, target_service, status, requested_by, requested_at,
                    completed_at, original_latency_ms, replay_latency_ms,
                    original_result_count, replay_result_count, results_match, diff, error
                ) VALUES (
                    $1::uuid, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz,
                    $8, $9, $10, $11, $12, $13::jsonb, $14
                )
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    completed_at = EXCLUDED.completed_at,
                    replay_latency_ms = EXCLUDED.replay_latency_ms,
                    replay_result_count = EXCLUDED.replay_result_count,
                    results_match = EXCLUDED.results_match,
                    diff = EXCLUDED.diff,
                    error = EXCLUDED.error
                """,
                record["id"],
                record["query_id"],
                record["target_service"],
                record["status"],
                record["requested_by"],
                record["requested_at"],
                record["completed_at"],
                (record["original"] or {}).get("latency_ms"),
                (record["replay"] or {}).get("latency_ms"),
                (record["original"] or {}).get("result_count"),
                (record["replay"] or {}).get("result_count"),
                (record["diff"] or {}).get("results_match"),
                json.dumps(record["diff"]) if record["diff"] else None,
                record["error"],
            )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        if self._pool is None:
            return self._memory.get(job_id)

        async with self._pool.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM replay_jobs WHERE id = $1::uuid", job_id)
        return dict(row) if row else None
