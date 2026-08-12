"""The ClickHouse query layer.

Every query is parameterised through ClickHouse's `param_<name>` mechanism —
values never reach the SQL string. This is a read-only service, but a gateway
that interpolates a user-supplied service name into SQL is one schema change
away from being a bigger problem, and parameterised queries also let ClickHouse
cache the query shape.

The one place a value does reach the SQL text is the group-by interval, which
is chosen from a fixed set of allowed values rather than passed through.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Allowed bucket sizes for time-series responses. Anything else is rejected
#: rather than substituted, so a caller's mistake is visible.
INTERVALS: dict[str, str] = {
    "1m": "toStartOfMinute(window_start)",
    "5m": "toStartOfFiveMinute(window_start)",
    "15m": "toStartOfFifteenMinutes(window_start)",
    "1h": "toStartOfHour(window_start)",
    "1d": "toStartOfDay(window_start)",
}

MAX_LIMIT = 1_000


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime

    @classmethod
    def last(cls, minutes: int) -> TimeRange:
        end = datetime.now(UTC)
        return cls(start=end - timedelta(minutes=minutes), end=end)

    def as_params(self) -> dict[str, str]:
        return {
            "start": self.start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": self.end.strftime("%Y-%m-%d %H:%M:%S"),
        }


class QueryError(RuntimeError):
    """The analytics store could not answer."""


class MetricsQueries:
    """Read-only queries against ClickHouse."""

    def __init__(
        self, url: str, database: str, user: str, password: str, client: Any = None
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._auth = (user, password)
        self._client = client

    async def _http(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def run(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        client = await self._http()
        request_params: dict[str, str] = {
            "database": self._database,
            "default_format": "JSONEachRow",
            # Without this ClickHouse returns 64-bit integers as JSON strings,
            # so every consumer would have to parse counts back to numbers.
            "output_format_json_quote_64bit_integers": "0",
        }
        for name, value in (params or {}).items():
            request_params[f"param_{name}"] = str(value)

        try:
            response = await client.post(
                self._url, params=request_params, content=sql.encode("utf-8"), auth=self._auth
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise QueryError(f"ClickHouse rejected the query: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise QueryError(f"ClickHouse is unreachable: {exc}") from exc

        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    @staticmethod
    def _bucket(interval: str) -> str:
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {sorted(INTERVALS)}, got {interval!r}")
        return INTERVALS[interval]

    @staticmethod
    def _service_filter(service: str | None) -> str:
        # The value is still parameterised; only the clause's presence varies.
        return "AND service = {service:String}" if service else ""

    async def latency(
        self, window: TimeRange, service: str | None = None, interval: str = "1m"
    ) -> list[dict[str, Any]]:
        """Latency percentiles per bucket.

        Percentiles are averaged across the rollups in a bucket rather than
        recomputed: the raw latencies are not in this table, and averaging p95s
        is an approximation. It is documented as such in the API response.
        """
        sql = f"""
            SELECT {self._bucket(interval)} AS bucket,
                   service,
                   round(avg(latency_p50), 2) AS p50,
                   round(avg(latency_p95), 2) AS p95,
                   round(avg(latency_p99), 2) AS p99,
                   round(avg(latency_avg), 2) AS avg,
                   round(max(latency_max), 2) AS max,
                   sum(query_count) AS queries
            FROM metric_rollups
            WHERE window_start BETWEEN {{start:DateTime}} AND {{end:DateTime}}
              {self._service_filter(service)}
            GROUP BY bucket, service
            ORDER BY bucket, service
        """
        params = window.as_params()
        if service:
            params["service"] = service
        return await self.run(sql, params)

    async def relevance(
        self, window: TimeRange, service: str | None = None, interval: str = "1m"
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._bucket(interval)} AS bucket,
                   service,
                   round(avg(relevance_avg), 4) AS avg_score,
                   round(avg(relevance_p10), 4) AS p10_score,
                   sum(query_count) AS queries
            FROM metric_rollups
            WHERE window_start BETWEEN {{start:DateTime}} AND {{end:DateTime}}
              AND relevance_avg IS NOT NULL
              {self._service_filter(service)}
            GROUP BY bucket, service
            ORDER BY bucket, service
        """
        params = window.as_params()
        if service:
            params["service"] = service
        return await self.run(sql, params)

    async def errors(
        self, window: TimeRange, service: str | None = None, interval: str = "1m"
    ) -> list[dict[str, Any]]:
        sql = f"""
            SELECT {self._bucket(interval)} AS bucket,
                   service,
                   sum(query_count) AS queries,
                   sum(error_count) AS errors,
                   round(sum(error_count) / nullIf(sum(query_count), 0), 6) AS error_rate
            FROM metric_rollups
            WHERE window_start BETWEEN {{start:DateTime}} AND {{end:DateTime}}
              {self._service_filter(service)}
            GROUP BY bucket, service
            ORDER BY bucket, service
        """
        params = window.as_params()
        if service:
            params["service"] = service
        return await self.run(sql, params)

    async def summary(self, window: TimeRange) -> dict[str, Any]:
        """The dashboard's overview card: one row for the whole window."""
        rows = await self.run(
            """
            SELECT sum(query_count) AS queries,
                   sum(error_count) AS errors,
                   round(sum(error_count) / nullIf(sum(query_count), 0), 6) AS error_rate,
                   round(avg(latency_p50), 2) AS p50,
                   round(avg(latency_p95), 2) AS p95,
                   round(avg(latency_p99), 2) AS p99,
                   round(avg(relevance_avg), 4) AS relevance,
                   round(avg(cache_hit_rate), 4) AS cache_hit_rate,
                   uniqExact(service) AS services
            FROM metric_rollups
            WHERE window_start BETWEEN {start:DateTime} AND {end:DateTime}
            """,
            window.as_params(),
        )
        anomalies = await self.run(
            """
            SELECT count() AS open_anomalies
            FROM anomalies
            WHERE window_start BETWEEN {start:DateTime} AND {end:DateTime}
            """,
            window.as_params(),
        )

        summary = rows[0] if rows else {}
        # Tolerate an unexpected shape: a missing count must not 500 the card.
        summary["open_anomalies"] = anomalies[0].get("open_anomalies", 0) if anomalies else 0
        return summary

    async def services(self, window: TimeRange) -> list[dict[str, Any]]:
        return await self.run(
            """
            SELECT service,
                   sum(query_count) AS queries,
                   round(avg(latency_p95), 2) AS p95,
                   round(sum(error_count) / nullIf(sum(query_count), 0), 6) AS error_rate
            FROM metric_rollups
            WHERE window_start BETWEEN {start:DateTime} AND {end:DateTime}
            GROUP BY service
            ORDER BY queries DESC
            """,
            window.as_params(),
        )

    async def anomalies(
        self,
        window: TimeRange,
        service: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, MAX_LIMIT))
        offset = max(0, offset)

        filters = ""
        params = window.as_params() | {"limit": limit, "offset": offset}
        if service:
            filters += " AND service = {service:String}"
            params["service"] = service
        if severity:
            filters += " AND severity = {severity:String}"
            params["severity"] = severity

        return await self.run(
            f"""
            SELECT anomaly_id, service, metric,
                   toString(window_start) AS window_start,
                   toString(window_end) AS window_end,
                   observed, baseline_mean, baseline_stddev,
                   round(z_score, 3) AS z_score, severity, sample_count,
                   toString(detected_at) AS detected_at
            FROM anomalies
            WHERE window_start BETWEEN {{start:DateTime}} AND {{end:DateTime}}
              {filters}
            ORDER BY detected_at DESC
            LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}
            """,
            params,
        )

    async def slowest_queries(
        self, window: TimeRange, service: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """The entry point into debugging: which queries were worst."""
        limit = max(1, min(limit, MAX_LIMIT))
        params = window.as_params() | {"limit": limit}
        if service:
            params["service"] = service

        return await self.run(
            f"""
            SELECT query_id, trace_id, service, query, latency_ms, status,
                   result_count, relevance_score, toString(timestamp) AS timestamp
            FROM events
            WHERE timestamp BETWEEN {{start:DateTime}} AND {{end:DateTime}}
              {self._service_filter(service)}
            ORDER BY latency_ms DESC
            LIMIT {{limit:UInt32}}
            """,
            params,
        )

    async def ping(self) -> bool:
        try:
            await self.run("SELECT 1")
            return True
        except QueryError:
            return False

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()
