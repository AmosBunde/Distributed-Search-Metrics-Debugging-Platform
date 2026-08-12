"""Batched, retrying writes to ClickHouse.

ClickHouse wants few large inserts, not many small ones (ADR-0002), so rows are
buffered until either the batch size or the flush interval is reached.

A failed flush keeps its rows and re-raises. The caller must not commit Kafka
offsets for work that did not land — that is the whole basis of at-least-once
here, and `ReplacingMergeTree` makes the resulting duplicate insert harmless.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ClickHouseWriter:
    """Buffers rows per table and flushes them as JSONEachRow inserts."""

    def __init__(
        self,
        url: str,
        database: str,
        user: str,
        password: str,
        batch_size: int = 1_000,
        flush_interval_seconds: float = 5.0,
        max_retries: int = 5,
        client: Any = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._url = url.rstrip("/")
        self._database = database
        self._auth = (user, password)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._max_retries = max_retries
        self._clock = clock
        self._last_flush = clock()
        self._client = client
        self._buffers: dict[str, list[dict[str, Any]]] = {}

    async def _http(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def buffered(self, table: str | None = None) -> int:
        if table is not None:
            return len(self._buffers.get(table, []))
        return sum(len(rows) for rows in self._buffers.values())

    def add(self, table: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._buffers.setdefault(table, []).extend(rows)

    @property
    def should_flush(self) -> bool:
        """Flush on size *or* age.

        Age matters as much as size: under light traffic a few raw events would
        otherwise sit in the buffer indefinitely, and those events are exactly
        what someone debugging a specific slow query is looking for.
        """
        if not self.buffered():
            return False
        if self.buffered() >= self._batch_size:
            return True
        return (self._clock() - self._last_flush) >= self._flush_interval

    async def execute(self, query: str) -> str:
        client = await self._http()
        response = await client.post(
            self._url,
            params={"database": self._database},
            content=query.encode("utf-8"),
            auth=self._auth,
        )
        response.raise_for_status()
        return response.text

    async def _insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        payload = "\n".join(json.dumps(row, default=str) for row in rows)
        client = await self._http()
        response = await client.post(
            self._url,
            params={
                "database": self._database,
                "query": f"INSERT INTO {table} FORMAT JSONEachRow",
            },
            content=payload.encode("utf-8"),
            auth=self._auth,
        )
        response.raise_for_status()

    async def flush(self) -> int:
        """Write every buffered row. Retries with backoff; re-raises on failure.

        Rows stay buffered if the insert fails, so the caller can decline to
        commit offsets and the data is not lost.
        """
        written = 0
        self._last_flush = self._clock()

        for table, rows in list(self._buffers.items()):
            if not rows:
                continue

            for attempt in range(1, self._max_retries + 1):
                try:
                    await self._insert(table, rows)
                    written += len(rows)
                    self._buffers[table] = []
                    break
                except Exception as exc:
                    if attempt == self._max_retries:
                        logger.error(
                            "clickhouse insert failed permanently",
                            extra={"table": table, "rows": len(rows), "attempts": attempt},
                        )
                        raise
                    delay = min(2 ** (attempt - 1), 30)
                    logger.warning(
                        "clickhouse insert failed, retrying",
                        extra={
                            "table": table,
                            "rows": len(rows),
                            "attempt": attempt,
                            "retry_in_seconds": delay,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(delay)

        return written

    async def close(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()
