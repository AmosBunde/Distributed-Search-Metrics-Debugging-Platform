"""The processing step, separated from the Kafka loop that drives it.

`process_batch` takes decoded records and returns everything that should be
written and published. Keeping it free of consumers and producers means the
behaviour that matters — what lands in ClickHouse, what becomes an anomaly — is
testable without any infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from search_metrics_common import AnomalyEvent, SearchEvent

from .aggregation import MetricRollup, WindowedAggregator
from .anomaly import AnomalyDetector

logger = logging.getLogger(__name__)


def event_row(event: SearchEvent) -> dict[str, Any]:
    """A `search_metrics.events` row."""
    received_at = event.metadata.get("received_at") or event.timestamp.isoformat()
    return {
        "event_id": str(event.event_id),
        "query_id": event.query_id,
        "trace_id": event.trace_id or "",
        "span_id": event.span_id or "",
        "service": event.service,
        "query": event.query,
        "index_name": event.index or "",
        "timestamp": event.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "received_at": str(received_at).replace("T", " ")[:23],
        "latency_ms": event.latency_ms,
        "status": str(event.status),
        "result_count": event.result_count,
        "relevance_score": event.relevance_score,
        "cache_hit": int(event.cache_hit),
        "user_id": event.user_id or "",
        "session_id": event.session_id or "",
        "error_type": event.error_type or "",
        "error_message": event.error_message or "",
    }


def anomaly_row(anomaly: AnomalyEvent) -> dict[str, Any]:
    return {
        "anomaly_id": str(anomaly.anomaly_id),
        "service": anomaly.service,
        "metric": anomaly.metric,
        "window_start": anomaly.window_start.strftime("%Y-%m-%d %H:%M:%S"),
        "window_end": anomaly.window_end.strftime("%Y-%m-%d %H:%M:%S"),
        "observed": anomaly.observed,
        "baseline_mean": anomaly.baseline_mean,
        "baseline_stddev": anomaly.baseline_stddev,
        "z_score": anomaly.z_score,
        "severity": str(anomaly.severity),
        "sample_count": anomaly.sample_count,
        "detected_at": anomaly.detected_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


def result_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """`search_metrics.query_results` rows from a results record."""
    timestamp = str(payload.get("timestamp", "")).replace("T", " ")[:23]
    return [
        {
            "query_id": payload["query_id"],
            "service": payload.get("service", ""),
            "timestamp": timestamp,
            "document_id": result.get("document_id", ""),
            "rank": result.get("rank", 0),
            "score": result.get("score", 0.0),
            "title": result.get("title") or "",
        }
        for result in payload.get("results", [])
    ]


def span_row(payload: dict[str, Any]) -> dict[str, Any]:
    """A `search_metrics.spans` row."""
    start = str(payload.get("start_time", "")).replace("T", " ").replace("+00:00", "")
    return {
        "trace_id": payload["trace_id"],
        "span_id": payload["span_id"],
        "parent_span_id": payload.get("parent_span_id", ""),
        "query_id": payload.get("query_id", ""),
        "service": payload.get("service", ""),
        "operation": payload.get("operation", ""),
        "start_time": start[:26],
        "duration_ms": payload.get("duration_ms", 0.0),
        "status": payload.get("status", "ok"),
        "attributes": payload.get("attributes", {}),
    }


def is_span_record(payload: dict[str, Any]) -> bool:
    """Spans carry a span id; nothing else on the stream does."""
    return "span_id" in payload and "operation" in payload


def is_results_record(payload: dict[str, Any]) -> bool:
    """Results records carry documents but no latency; events are the reverse."""
    return "results" in payload and "latency_ms" not in payload


@dataclass
class BatchOutcome:
    """Everything one batch produced."""

    events: list[SearchEvent] = field(default_factory=list)
    rollups: list[MetricRollup] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[AnomalyEvent] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    invalid: int = 0

    @property
    def processed(self) -> int:
        return len(self.events)


class Pipeline:
    """Decode, aggregate, close windows and score anomalies."""

    def __init__(self, aggregator: WindowedAggregator, detector: AnomalyDetector) -> None:
        self._aggregator = aggregator
        self._detector = detector

    def process_batch(self, payloads: list[dict[str, Any]]) -> BatchOutcome:
        outcome = BatchOutcome()

        for payload in payloads:
            # One consumer group reads three topics, so records are routed by
            # shape rather than by which topic they arrived on.
            if is_span_record(payload):
                outcome.spans.append(span_row(payload))
                continue

            if is_results_record(payload):
                outcome.results.extend(result_rows(payload))
                continue

            try:
                event = SearchEvent.model_validate(payload)
            except ValidationError:
                # The collector validates on the way in, so this means a
                # producer bypassed it or the schema changed under us. Drop the
                # record rather than stalling the partition, and count it.
                outcome.invalid += 1
                logger.warning("dropping unreadable event from the stream")
                continue

            outcome.events.append(event)
            self._aggregator.add(event)

        outcome.rollups = self._aggregator.close_ready()
        outcome.anomalies = self._detector.evaluate_all(outcome.rollups)
        return outcome

    def flush_windows(self) -> BatchOutcome:
        """Close every open window — used on shutdown."""
        rollups = self._aggregator.flush()
        return BatchOutcome(rollups=rollups, anomalies=self._detector.evaluate_all(rollups))

    @property
    def open_windows(self) -> int:
        return self._aggregator.open_windows
