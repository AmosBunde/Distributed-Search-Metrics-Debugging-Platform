"""Windowed aggregation: events in, rollups out.

Deliberately pure. Nothing here touches Kafka or ClickHouse, so the arithmetic
that every dashboard number depends on can be tested by handing it a list of
events and comparing the result to one worked out by hand.

Windows are tumbling and aligned to the epoch, so every replica of the engine
agrees on window boundaries without coordinating. Events are placed by their
reported timestamp; a badly delayed event lands in the window it claims rather
than the one it arrived in, and if that window has already been emitted the
rollup is recomputed and replaces the old one (ADR-0003).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from search_metrics_common import SearchEvent, SearchStatus


def window_start_for(timestamp: datetime, window_seconds: int) -> datetime:
    """Epoch-aligned start of the window a timestamp belongs to."""
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    epoch_seconds = int(timestamp.timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % window_seconds), tz=UTC)


def percentile(sorted_values: list[float], quantile: float) -> float:
    """Nearest-rank percentile of an already sorted list.

    Nearest-rank rather than interpolated: every reported latency is a value that
    actually occurred, which is what an operator expects when they click through
    from "p99 was 2.3s" to the traces behind it.
    """
    if not sorted_values:
        return 0.0
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")

    rank = max(1, min(len(sorted_values), int(-(-len(sorted_values) * quantile // 1))))
    return sorted_values[rank - 1]


@dataclass
class MetricRollup:
    """One service's metrics over one window — a row of `metric_rollups`."""

    window_start: datetime
    window_end: datetime
    service: str
    query_count: int
    error_count: int
    error_rate: float
    latency_p50: float
    latency_p95: float
    latency_p99: float
    latency_avg: float
    latency_max: float
    relevance_avg: float | None
    relevance_p10: float | None
    cache_hit_rate: float

    def as_row(self) -> dict[str, object]:
        """ClickHouse JSONEachRow representation."""
        return {
            "window_start": self.window_start.strftime("%Y-%m-%d %H:%M:%S"),
            "window_end": self.window_end.strftime("%Y-%m-%d %H:%M:%S"),
            "service": self.service,
            "query_count": self.query_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 6),
            "latency_p50": round(self.latency_p50, 3),
            "latency_p95": round(self.latency_p95, 3),
            "latency_p99": round(self.latency_p99, 3),
            "latency_avg": round(self.latency_avg, 3),
            "latency_max": round(self.latency_max, 3),
            "relevance_avg": None if self.relevance_avg is None else round(self.relevance_avg, 6),
            "relevance_p10": None if self.relevance_p10 is None else round(self.relevance_p10, 6),
            "cache_hit_rate": round(self.cache_hit_rate, 6),
        }


@dataclass
class WindowAccumulator:
    """Running state for one (service, window) pair.

    Latencies are kept rather than summarised because percentiles cannot be
    computed incrementally without either approximation or the full sample. At
    60-second windows this is a few thousand floats per service — cheap, and
    exact.
    """

    service: str
    window_start: datetime
    window_seconds: int
    latencies: list[float] = field(default_factory=list)
    relevance_scores: list[float] = field(default_factory=list)
    query_count: int = 0
    error_count: int = 0
    cache_hits: int = 0

    @property
    def window_end(self) -> datetime:
        return self.window_start + timedelta(seconds=self.window_seconds)

    def add(self, event: SearchEvent) -> None:
        self.query_count += 1
        self.latencies.append(event.latency_ms)

        if event.status is not SearchStatus.OK:
            self.error_count += 1
        if event.cache_hit:
            self.cache_hits += 1
        if event.relevance_score is not None:
            self.relevance_scores.append(event.relevance_score)

    def to_rollup(self) -> MetricRollup:
        latencies = sorted(self.latencies)
        relevance = sorted(self.relevance_scores)

        return MetricRollup(
            window_start=self.window_start,
            window_end=self.window_end,
            service=self.service,
            query_count=self.query_count,
            error_count=self.error_count,
            error_rate=self.error_count / self.query_count if self.query_count else 0.0,
            latency_p50=percentile(latencies, 0.50),
            latency_p95=percentile(latencies, 0.95),
            latency_p99=percentile(latencies, 0.99),
            latency_avg=sum(latencies) / len(latencies) if latencies else 0.0,
            latency_max=latencies[-1] if latencies else 0.0,
            relevance_avg=sum(relevance) / len(relevance) if relevance else None,
            relevance_p10=percentile(relevance, 0.10) if relevance else None,
            cache_hit_rate=self.cache_hits / self.query_count if self.query_count else 0.0,
        )


class WindowedAggregator:
    """Accumulates events into windows and releases them once they close.

    A window is closed when the watermark — the latest event timestamp seen,
    less a grace period — has moved past its end. Grace exists because events
    from different services arrive slightly out of order; without it, a window
    would be emitted while stragglers were still arriving and immediately need
    rewriting.
    """

    def __init__(self, window_seconds: int = 60, grace_seconds: int = 5) -> None:
        self.window_seconds = window_seconds
        self.grace_seconds = grace_seconds
        self._windows: dict[tuple[str, datetime], WindowAccumulator] = {}
        self._watermark: datetime | None = None

    def add(self, event: SearchEvent) -> None:
        start = window_start_for(event.timestamp, self.window_seconds)
        key = (event.service, start)

        accumulator = self._windows.get(key)
        if accumulator is None:
            accumulator = WindowAccumulator(event.service, start, self.window_seconds)
            self._windows[key] = accumulator

        accumulator.add(event)

        if self._watermark is None or event.timestamp > self._watermark:
            self._watermark = event.timestamp

    def close_ready(self, now: datetime | None = None) -> list[MetricRollup]:
        """Emit and forget every window the watermark has moved past."""
        reference = now or self._watermark
        if reference is None:
            return []

        cutoff = reference - timedelta(seconds=self.grace_seconds)
        ready = [
            key for key, accumulator in self._windows.items() if accumulator.window_end <= cutoff
        ]

        rollups = [self._windows.pop(key).to_rollup() for key in sorted(ready, key=lambda k: k[1])]
        return rollups

    def flush(self) -> list[MetricRollup]:
        """Emit every open window. Used on shutdown so nothing is lost."""
        rollups = [
            self._windows[key].to_rollup() for key in sorted(self._windows, key=lambda k: k[1])
        ]
        self._windows.clear()
        return rollups

    @property
    def open_windows(self) -> int:
        return len(self._windows)


def aggregate(events: list[SearchEvent], window_seconds: int = 60) -> list[MetricRollup]:
    """Aggregate a complete batch in one call — the deterministic entry point."""
    accumulators: dict[tuple[str, datetime], WindowAccumulator] = {}
    grouped: dict[tuple[str, datetime], list[SearchEvent]] = defaultdict(list)

    for event in events:
        grouped[(event.service, window_start_for(event.timestamp, window_seconds))].append(event)

    for (service, start), window_events in grouped.items():
        accumulator = WindowAccumulator(service, start, window_seconds)
        for event in window_events:
            accumulator.add(event)
        accumulators[(service, start)] = accumulator

    return [
        accumulators[key].to_rollup() for key in sorted(accumulators, key=lambda k: (k[1], k[0]))
    ]
