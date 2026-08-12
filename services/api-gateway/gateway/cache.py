"""Read-through caching for metric queries.

Dashboards poll. Ten operators watching the same overview should not each
trigger the same ClickHouse scan, so answers are cached in Redis by query shape.

TTLs are short and deliberately uneven: a summary card can be a few seconds
stale without anyone noticing, while an anomaly feed going stale is the one
thing an on-call engineer would actually mind.

Cache failures are never fatal. Redis being down makes the dashboard slower,
not broken.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Seconds, per endpoint.
TTL_SECONDS: dict[str, int] = {
    "summary": 10,
    "latency": 15,
    "relevance": 30,
    "errors": 15,
    "services": 30,
    "anomalies": 5,
    "slowest": 15,
}
DEFAULT_TTL = 15


def cache_key(endpoint: str, **parameters: Any) -> str:
    """A stable key for one query shape.

    The parameters are hashed rather than concatenated so that a long time range
    or an unusual service name cannot produce an unbounded key.
    """
    payload = json.dumps(parameters, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"gateway:{endpoint}:{digest}"


class MetricsCache:
    """Read-through cache. With no Redis it is a no-op that always misses."""

    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def get_or_set(self, endpoint: str, loader, **parameters: Any) -> Any:
        if self._redis is None:
            self.misses += 1
            return await loader()

        key = cache_key(endpoint, **parameters)

        try:
            cached = await self._redis.get(key)
            if cached is not None:
                self.hits += 1
                return json.loads(cached)
        except Exception:
            logger.warning("cache read failed, falling through to ClickHouse", exc_info=True)

        self.misses += 1
        value = await loader()

        try:
            await self._redis.setex(
                key, TTL_SECONDS.get(endpoint, DEFAULT_TTL), json.dumps(value, default=str)
            )
        except Exception:
            logger.warning("cache write failed", exc_info=True)

        return value

    async def invalidate(self, endpoint: str) -> int:
        """Drop every cached answer for one endpoint."""
        if self._redis is None:
            return 0

        removed = 0
        try:
            async for key in self._redis.scan_iter(match=f"gateway:{endpoint}:*"):
                await self._redis.delete(key)
                removed += 1
        except Exception:
            logger.warning("cache invalidation failed", exc_info=True)
        return removed

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
