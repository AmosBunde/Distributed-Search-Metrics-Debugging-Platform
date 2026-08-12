"""Shared building blocks for the search metrics platform services.

Anything that crosses a service boundary — the event contract, configuration,
log format, trace propagation, Kafka conventions — is defined here exactly once.
"""

from .kafka import EventPublisher, build_consumer, build_producer, producer_context
from .logging import configure_logging
from .models import (
    MAX_BATCH_SIZE,
    AnomalyEvent,
    IngestResult,
    SearchEvent,
    SearchEventBatch,
    SearchResult,
    SearchStatus,
    Severity,
)
from .settings import Settings, get_settings
from .topics import ALL_TOPICS, Topic
from .tracing import (
    configure_tracing,
    current_trace_id,
    extract_trace_context,
    inject_trace_context,
    instrument_fastapi,
)

__all__ = [
    "ALL_TOPICS",
    "MAX_BATCH_SIZE",
    "AnomalyEvent",
    "EventPublisher",
    "IngestResult",
    "SearchEvent",
    "SearchEventBatch",
    "SearchResult",
    "SearchStatus",
    "Settings",
    "Severity",
    "Topic",
    "build_consumer",
    "build_producer",
    "configure_logging",
    "configure_tracing",
    "current_trace_id",
    "extract_trace_context",
    "get_settings",
    "inject_trace_context",
    "instrument_fastapi",
    "producer_context",
]
