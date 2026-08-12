"""Generating search events that look like real traffic.

Realism matters in two specific ways, and both are about not flattering the
platform:

* **Latency is log-normal, not uniform.** Real search latency has a long right
  tail, which is exactly why anyone cares about p99. Uniform noise would make
  the percentile panels meaningless and the anomaly detector look better than it
  is.
* **A failed query still carries a latency.** Errors that arrive as zero-latency
  events would quietly drag every percentile down.

Everything is driven by a seeded `random.Random`, so the same seed produces the
same traffic — a test can assert on it, and a scenario can be reproduced.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import UTC, datetime
from typing import Any

from .scenarios import SERVICE_LATENCY, SERVICES, Phase

QUERY_TERMS: tuple[str, ...] = (
    "distributed tracing", "kafka consumer lag", "clickhouse materialised view",
    "p99 latency", "opentelemetry python", "search relevance tuning",
    "kubernetes autoscaling", "redis eviction policy", "terraform state locking",
    "grafana alert rules", "index sharding strategy", "query rewriting",
    "vector search", "bm25 ranking", "typeahead suggestions", "spell correction",
    "faceted navigation", "result deduplication", "cold start latency",
    "circuit breaker pattern",
)  # fmt: skip

ERROR_TYPES: tuple[str, ...] = (
    "UpstreamUnavailable",
    "ShardTimeout",
    "QueryParseError",
    "IndexNotReady",
    "RateLimited",
)

INDICES: tuple[str, ...] = ("documents", "products", "help-centre")


def log_normal_latency(rng: random.Random, median_ms: float, sigma: float) -> float:
    """A latency with a realistic long tail.

    `median_ms` is the median rather than the mean: for a log-normal the mean
    sits above the median, and describing the input as a median is what makes
    the generated p50 land where a reader expects.
    """
    return math.exp(math.log(median_ms) + rng.gauss(0.0, sigma))


def generate_event(
    rng: random.Random,
    phase: Phase,
    service: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """One search event, shaped by the phase in effect."""
    service = service or rng.choice(SERVICES)
    median, sigma = SERVICE_LATENCY[service]
    affected = phase.applies_to(service)

    latency = log_normal_latency(rng, median, sigma)
    if affected:
        latency *= phase.latency_multiplier

    roll = rng.random()
    error_rate = phase.error_rate if affected else 0.005
    timeout_rate = phase.timeout_rate if affected else 0.001

    status = "ok"
    error_type: str | None = None
    if roll < timeout_rate:
        status = "timeout"
        error_type = "ShardTimeout"
        # A timeout is slow by definition; reporting it as fast would be a lie.
        latency = max(latency, 3_000.0)
    elif roll < timeout_rate + error_rate:
        status = "error"
        error_type = rng.choice(ERROR_TYPES)

    query = rng.choice(QUERY_TERMS)
    cache_hit = status == "ok" and rng.random() < phase.cache_hit_rate
    if cache_hit:
        latency *= 0.25  # a cache hit skips the expensive path

    event: dict[str, Any] = {
        "query_id": f"q-{uuid.uuid4().hex[:16]}",
        "service": service,
        "query": query,
        "index": rng.choice(INDICES),
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
        "latency_ms": round(min(latency, 599_000.0), 3),
        "status": status,
        "cache_hit": cache_hit,
        "session_id": f"s-{rng.randrange(1, 5_000)}",
    }

    if status == "ok":
        result_count = rng.randrange(0, 25)
        relevance = min(1.0, max(0.0, rng.betavariate(6, 2) + phase.relevance_shift))
        event["result_count"] = result_count
        event["relevance_score"] = round(relevance, 4)
        if result_count:
            event["results"] = [
                {
                    "document_id": f"doc-{rng.randrange(1, 100_000)}",
                    "rank": rank,
                    "score": round(max(0.0, relevance - rank * 0.03), 4),
                }
                for rank in range(1, min(result_count, 5) + 1)
            ]
    else:
        event["result_count"] = 0
        event["error_type"] = error_type
        event["error_message"] = f"{error_type} while querying {service}"

    return event


def generate_batch(
    rng: random.Random, phase: Phase, size: int, timestamp: datetime | None = None
) -> list[dict[str, Any]]:
    return [generate_event(rng, phase, timestamp=timestamp) for _ in range(size)]
