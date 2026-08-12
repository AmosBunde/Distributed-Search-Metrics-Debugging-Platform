"""The event contract is the platform's narrowest waist — it gets the most tests.

Each test here corresponds to a way a bad event could silently produce a wrong
metric rather than an obvious failure.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from search_metrics_common.models import (
    MAX_BATCH_SIZE,
    MAX_LATENCY_MS,
    AnomalyEvent,
    IngestResult,
    SearchEvent,
    SearchEventBatch,
    SearchResult,
    SearchStatus,
    Severity,
)
from search_metrics_common.topics import Topic

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def make_event(**overrides) -> SearchEvent:
    payload = {
        "query_id": "q-1",
        "service": "search-api",
        "query": "distributed tracing",
        "latency_ms": 42.5,
        "timestamp": NOW,
        "result_count": 3,
        "relevance_score": 0.82,
    }
    payload.update(overrides)
    return SearchEvent(**payload)


class TestSearchEvent:
    def test_minimal_event_is_accepted(self) -> None:
        event = SearchEvent(query_id="q", service="s", query="hello", latency_ms=1)
        assert event.status is SearchStatus.OK
        assert event.result_count == 0
        assert event.event_id is not None

    def test_negative_latency_is_rejected(self) -> None:
        """A negative latency would drag every percentile it lands in downwards."""
        with pytest.raises(ValidationError):
            make_event(latency_ms=-1)

    def test_absurd_latency_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_event(latency_ms=MAX_LATENCY_MS + 1)

    @pytest.mark.parametrize("score", [-0.01, 1.01, 42])
    def test_relevance_score_outside_its_range_is_rejected(self, score: float) -> None:
        with pytest.raises(ValidationError):
            make_event(relevance_score=score)

    def test_empty_query_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_event(query="")

    def test_missing_query_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchEvent(service="s", query="q", latency_ms=1)

    def test_unknown_field_is_rejected(self) -> None:
        """Schema drift should fail at ingest, not surface as a missing column."""
        with pytest.raises(ValidationError):
            make_event(latencyMS=5)

    def test_naive_timestamp_is_rejected(self) -> None:
        """A naive timestamp lands in whichever window the reader's clock implies."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_event(timestamp=datetime(2026, 8, 12, 10, 0))

    def test_timestamp_is_normalised_to_utc(self) -> None:
        event = make_event(timestamp=datetime(2026, 8, 12, 12, 0, tzinfo=timezone_plus_two()))
        assert event.timestamp == NOW

    def test_successful_event_must_not_carry_error_details(self) -> None:
        with pytest.raises(ValidationError, match="must not carry error details"):
            make_event(status=SearchStatus.OK, error_type="Timeout")

    @pytest.mark.parametrize(
        "status", [SearchStatus.ERROR, SearchStatus.TIMEOUT, SearchStatus.REJECTED]
    )
    def test_failed_event_requires_an_error_type(self, status: SearchStatus) -> None:
        with pytest.raises(ValidationError, match="requires error_type"):
            make_event(status=status)

    def test_results_cannot_exceed_the_reported_count(self) -> None:
        with pytest.raises(ValidationError, match="more entries than result_count"):
            make_event(
                result_count=1,
                results=[
                    SearchResult(document_id="a", rank=1, score=0.9),
                    SearchResult(document_id="b", rank=2, score=0.8),
                ],
            )

    def test_duplicate_ranks_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ranks must be unique"):
            make_event(
                result_count=2,
                results=[
                    SearchResult(document_id="a", rank=1, score=0.9),
                    SearchResult(document_id="b", rank=1, score=0.8),
                ],
            )


class TestRouting:
    def test_successful_events_go_to_the_events_topic(self) -> None:
        assert make_event().topic is Topic.EVENTS

    @pytest.mark.parametrize(
        "status", [SearchStatus.ERROR, SearchStatus.TIMEOUT, SearchStatus.REJECTED]
    )
    def test_failures_go_to_the_errors_topic(self, status: SearchStatus) -> None:
        event = make_event(status=status, error_type="UpstreamTimeout")
        assert event.topic is Topic.ERRORS
        assert event.is_failure

    def test_partition_key_is_the_query_id(self) -> None:
        """One query's records must stay on one partition to keep their order."""
        assert make_event(query_id="q-99").partition_key == "q-99"


class TestBatch:
    def test_batch_at_the_limit_is_accepted(self) -> None:
        batch = SearchEventBatch(events=[make_event() for _ in range(MAX_BATCH_SIZE)])
        assert len(batch.events) == MAX_BATCH_SIZE

    def test_oversized_batch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchEventBatch(events=[make_event() for _ in range(MAX_BATCH_SIZE + 1)])

    def test_empty_batch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SearchEventBatch(events=[])


class TestAnomalyEvent:
    def make_anomaly(self, **overrides) -> AnomalyEvent:
        payload = {
            "service": "search-api",
            "metric": "latency_p95",
            "window_start": NOW,
            "window_end": NOW + timedelta(seconds=60),
            "observed": 950.0,
            "baseline_mean": 120.0,
            "baseline_stddev": 15.0,
            "z_score": 55.3,
            "sample_count": 480,
        }
        payload.update(overrides)
        return AnomalyEvent(**payload)

    def test_window_must_move_forwards(self) -> None:
        with pytest.raises(ValidationError, match="window_end must be after"):
            self.make_anomaly(window_end=NOW - timedelta(seconds=1))

    def test_zero_length_window_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.make_anomaly(window_end=NOW)

    def test_negative_stddev_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.make_anomaly(baseline_stddev=-1)

    def test_signature_groups_repeats_of_one_anomaly(self) -> None:
        """Suppression depends on two firings of the same problem matching."""
        first = self.make_anomaly(severity=Severity.CRITICAL)
        second = self.make_anomaly(severity=Severity.CRITICAL, observed=1200.0)
        assert first.signature == second.signature
        assert first.signature != self.make_anomaly(metric="error_rate").signature


def test_ingest_result_totals() -> None:
    result = IngestResult(accepted=8, rejected=2, errors=["event 3: bad latency"])
    assert result.total == 10


def timezone_plus_two():
    from datetime import timedelta as td
    from datetime import timezone as tz

    return tz(td(hours=2))
