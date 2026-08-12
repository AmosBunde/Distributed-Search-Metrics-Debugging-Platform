"""Token-bucket rate limiting.

Two implementations behind one interface:

* :class:`RedisTokenBucket` — the real one. The bucket lives in Redis and is
  refilled and consumed inside a Lua script, so the read-modify-write is atomic
  and several collector replicas share one limit per client.
* :class:`InMemoryTokenBucket` — the fallback. Correct for a single process,
  which makes it right for local development and tests and wrong for a
  multi-replica deployment, where each replica would allow the full rate.

If Redis is configured but unreachable, the limiter **allows** the request and
logs. Dropping telemetry because the limiter is down would turn a cache outage
into data loss.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Refill and consume in one round trip. KEYS[1] is the bucket; ARGV carries the
# rate, capacity, current time and the cost of this request.
_TOKEN_BUCKET_LUA = """
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local tokens = tonumber(bucket[1])
local updated = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  updated = now
end

tokens = math.min(capacity, tokens + (now - updated) * rate)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 60)

local retry_after = 0
if allowed == 0 then
  retry_after = math.ceil((cost - tokens) / rate)
end
return {allowed, retry_after}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    async def check(self, key: str, cost: int = 1) -> RateLimitDecision: ...


class NullRateLimiter:
    """Used when rate limiting is switched off."""

    async def check(self, key: str, cost: int = 1) -> RateLimitDecision:
        return RateLimitDecision(allowed=True)


class InMemoryTokenBucket:
    """Single-process token bucket.

    Correct only within one process: with several replicas each one allows the
    full rate, so this is a development and test fallback, not a deployment
    option.
    """

    def __init__(self, rate_per_second: float, capacity: int, clock=time.monotonic) -> None:
        self._rate = rate_per_second
        self._capacity = capacity
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    async def check(self, key: str, cost: int = 1) -> RateLimitDecision:
        now = self._clock()
        tokens, updated = self._buckets.get(key, (float(self._capacity), now))
        tokens = min(self._capacity, tokens + (now - updated) * self._rate)

        if tokens >= cost:
            self._buckets[key] = (tokens - cost, now)
            return RateLimitDecision(allowed=True)

        self._buckets[key] = (tokens, now)
        deficit = cost - tokens
        return RateLimitDecision(
            allowed=False, retry_after_seconds=max(1, int(deficit / self._rate) + 1)
        )


class RedisTokenBucket:
    """Token bucket shared by every replica, refilled atomically in Redis."""

    def __init__(self, redis: Any, rate_per_second: float, capacity: int) -> None:
        self._redis = redis
        self._rate = rate_per_second
        self._capacity = capacity
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    async def check(self, key: str, cost: int = 1) -> RateLimitDecision:
        try:
            allowed, retry_after = await self._script(
                keys=[f"ratelimit:{key}"],
                args=[self._rate, self._capacity, time.time(), cost],
            )
        except Exception:
            # Fail open: a limiter outage must not become telemetry loss.
            logger.warning("rate limiter unavailable, allowing request", exc_info=True)
            return RateLimitDecision(allowed=True)

        return RateLimitDecision(
            allowed=bool(allowed), retry_after_seconds=max(1, int(retry_after))
        )


def build_rate_limiter(settings: Any, redis: Any | None = None) -> RateLimiter:
    """Pick the limiter that matches the configuration and what is available."""
    if not settings.rate_limit_enabled:
        return NullRateLimiter()

    rate_per_second = settings.rate_limit_requests_per_minute / 60.0
    if redis is not None:
        return RedisTokenBucket(redis, rate_per_second, settings.rate_limit_burst)

    logger.warning("rate limiting without Redis: limits apply per replica, not per deployment")
    return InMemoryTokenBucket(rate_per_second, settings.rate_limit_burst)
