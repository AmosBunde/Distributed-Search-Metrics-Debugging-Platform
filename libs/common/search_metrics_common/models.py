"""The event contract every service agrees on.

These models are the only place the shape of a search event is defined. Services
validate against them at their edges, so a malformed event is rejected at ingest
rather than corrupting a rollup three stages later.

Validation is deliberately strict about the things that would silently produce
wrong metrics: a negative latency, a relevance score outside its range, or an
absurd result count all fail loudly instead of skewing a percentile.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .topics import Topic

#: A single search must not be reported as taking longer than ten minutes; past
#: that the caller is reporting a bug, not a slow query.
MAX_LATENCY_MS = 600_000
MAX_QUERY_LENGTH = 1_024
MAX_RESULTS_PER_QUERY = 10_000
#: Batch ceiling enforced by the collector; also the documented API limit.
MAX_BATCH_SIZE = 500


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SearchStatus(StrEnum):
    """How a search finished. Anything but OK is routed to the errors topic."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PlatformModel(BaseModel):
    """Shared configuration: reject unknown fields so schema drift is caught."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


LatencyMs = Annotated[float, Field(ge=0, le=MAX_LATENCY_MS)]
RelevanceScore = Annotated[float, Field(ge=0.0, le=1.0)]


class SearchResult(PlatformModel):
    """One document returned for a query, with the score that ranked it."""

    document_id: str = Field(min_length=1, max_length=256)
    rank: int = Field(ge=1, le=MAX_RESULTS_PER_QUERY)
    score: RelevanceScore
    title: str | None = Field(default=None, max_length=512)


class SearchEvent(PlatformModel):
    """One executed search, as reported by an instrumented service.

    This is the unit of ingest: everything the platform measures is derived from
    a stream of these.
    """

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    query_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=64)
    span_id: str | None = Field(default=None, max_length=32)

    service: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    index: str | None = Field(default=None, max_length=64)

    timestamp: datetime = Field(default_factory=_utc_now)
    latency_ms: LatencyMs
    status: SearchStatus = SearchStatus.OK

    result_count: int = Field(default=0, ge=0, le=MAX_RESULTS_PER_QUERY)
    relevance_score: RelevanceScore | None = None
    results: list[SearchResult] = Field(default_factory=list)

    cache_hit: bool = False
    user_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)

    error_type: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None, max_length=2_048)

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """A naive timestamp would silently shift every window it lands in."""
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_consistency(self) -> SearchEvent:
        if self.status is SearchStatus.OK:
            if self.error_type or self.error_message:
                raise ValueError("a successful search must not carry error details")
        elif not self.error_type:
            raise ValueError(f"status {self.status} requires error_type")

        if self.results and len(self.results) > self.result_count:
            raise ValueError("results contains more entries than result_count reports")

        ranks = [r.rank for r in self.results]
        if len(set(ranks)) != len(ranks):
            raise ValueError("result ranks must be unique")
        return self

    @property
    def is_failure(self) -> bool:
        return self.status is not SearchStatus.OK

    @property
    def topic(self) -> Topic:
        """Which logical topic this event belongs on.

        The single source of truth for routing — the collector must not invent
        its own rule.
        """
        return Topic.ERRORS if self.is_failure else Topic.EVENTS

    @property
    def partition_key(self) -> str:
        """Keyed by query id so one query's records keep their order."""
        return self.query_id


class SearchEventBatch(PlatformModel):
    """A batch ingest request. Partial success is reported per entry."""

    events: list[SearchEvent] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


class AnomalyEvent(PlatformModel):
    """A window whose metric broke its own recent baseline."""

    anomaly_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    service: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=64)

    window_start: datetime
    window_end: datetime

    observed: float
    baseline_mean: float
    baseline_stddev: float = Field(ge=0)
    z_score: float
    severity: Severity = Severity.WARNING

    sample_count: int = Field(ge=0)
    detected_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def _check_window(self) -> AnomalyEvent:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self

    @property
    def signature(self) -> str:
        """Identity used to suppress duplicate alerts for one ongoing anomaly."""
        return f"{self.service}:{self.metric}:{self.severity}"


class IngestResult(PlatformModel):
    """What the collector reports back for one ingest call."""

    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.accepted + self.rejected
