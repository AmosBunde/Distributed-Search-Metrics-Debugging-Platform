"""Settings, logging and Kafka helpers — the parts every service inherits."""

import json
import logging
from datetime import UTC, datetime

import pytest
from search_metrics_common.kafka import EventPublisher, deserialize, serialize
from search_metrics_common.logging import JsonFormatter, configure_logging
from search_metrics_common.models import AnomalyEvent, SearchEvent, SearchResult, SearchStatus
from search_metrics_common.settings import Settings
from search_metrics_common.topics import ALL_TOPICS, Topic
from search_metrics_common.tracing import (
    configure_tracing,
    extract_trace_context,
    inject_trace_context,
)

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


class TestSettings:
    def test_defaults_target_the_local_stack(self) -> None:
        settings = Settings()
        assert settings.environment == "local"
        assert settings.window_seconds == 60
        assert settings.anomaly_zscore_threshold == 3.0

    def test_environment_variables_override_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("CLICKHOUSE_HOST", "clickhouse.internal")
        monkeypatch.setenv("WINDOW_SECONDS", "30")
        settings = Settings()
        assert settings.clickhouse_host == "clickhouse.internal"
        assert settings.window_seconds == 30

    def test_secrets_default_to_empty_not_to_something_that_works(self) -> None:
        settings = Settings()
        assert settings.clickhouse_password == ""
        assert settings.postgres_password == ""
        assert settings.redis_password == ""

    def test_redis_url_omits_credentials_when_there_is_no_password(self) -> None:
        assert Settings().redis_url == "redis://localhost:6379/0"

    def test_redis_url_includes_a_password_when_set(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
        assert Settings().redis_url.startswith("redis://:s3cret@")

    def test_postgres_dsn_is_assembled_from_parts(self, monkeypatch) -> None:
        monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
        assert Settings().postgres_dsn == (
            "postgresql://search:pw@localhost:5432/search_metrics_meta"
        )

    def test_bootstrap_servers_splits_a_comma_separated_list(self, monkeypatch) -> None:
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "a:9092, b:9092 ,")
        assert Settings().bootstrap_servers == ["a:9092", "b:9092"]

    @pytest.mark.parametrize("topic", ALL_TOPICS)
    def test_every_logical_topic_resolves_to_a_name(self, topic: Topic) -> None:
        assert Settings().topic_name(topic).startswith("search.")

    def test_topic_names_are_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("KAFKA_TOPIC_EVENTS", "staging.search.events")
        assert Settings().topic_name(Topic.EVENTS) == "staging.search.events"

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [("prod", True), ("production", True), ("Production", True), ("local", False)],
    )
    def test_production_detection(self, monkeypatch, environment: str, expected: bool) -> None:
        monkeypatch.setenv("ENVIRONMENT", environment)
        assert Settings().is_production is expected

    def test_invalid_values_are_rejected_at_startup(self, monkeypatch) -> None:
        """Better to fail on boot than to run with a nonsensical threshold."""
        monkeypatch.setenv("ANOMALY_ZSCORE_THRESHOLD", "0")
        with pytest.raises(ValueError):
            Settings()


class TestJsonLogging:
    def _record(self, **extra) -> logging.LogRecord:
        record = logging.LogRecord(
            name="collector", level=logging.INFO, pathname=__file__, lineno=1,
            msg="ingested %d events", args=(5,), exc_info=None,
        )  # fmt: skip
        record.__dict__.update(extra)
        return record

    def test_record_is_one_json_object(self) -> None:
        payload = json.loads(JsonFormatter("collector").format(self._record()))
        assert payload["message"] == "ingested 5 events"
        assert payload["level"] == "INFO"
        assert payload["service"] == "collector"

    def test_structured_context_is_included(self) -> None:
        payload = json.loads(JsonFormatter("collector").format(self._record(query_id="q-7")))
        assert payload["query_id"] == "q-7"

    def test_exceptions_are_rendered(self) -> None:
        try:
            raise RuntimeError("clickhouse unreachable")
        except RuntimeError:
            import sys

            record = self._record()
            record.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter("engine").format(record))
        assert "clickhouse unreachable" in payload["exception"]

    def test_configure_logging_replaces_handlers_rather_than_stacking_them(self) -> None:
        configure_logging("svc", "DEBUG")
        configure_logging("svc", "DEBUG")
        assert len(logging.getLogger().handlers) == 1
        assert logging.getLogger().level == logging.DEBUG


class TestSerialization:
    def test_models_round_trip(self) -> None:
        event = SearchEvent(query_id="q-1", service="s", query="hi", latency_ms=12.5, timestamp=NOW)
        restored = SearchEvent(**deserialize(serialize(event)))
        assert restored.query_id == event.query_id
        assert restored.timestamp == NOW

    def test_plain_mappings_serialize(self) -> None:
        assert deserialize(serialize({"a": 1})) == {"a": 1}


class FakeProducer:
    """Records what would have been sent, so publishing needs no broker."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_and_wait(self, topic, value=None, key=None, headers=None) -> None:
        self.sent.append({"topic": topic, "value": value, "key": key, "headers": headers})


class TestEventPublisher:
    @pytest.fixture
    def producer(self) -> FakeProducer:
        return FakeProducer()

    @pytest.fixture
    def publisher(self, producer: FakeProducer) -> EventPublisher:
        return EventPublisher(producer, Settings())

    @pytest.mark.asyncio
    async def test_successful_event_goes_to_the_events_topic(self, publisher, producer) -> None:
        await publisher.publish_event(
            SearchEvent(query_id="q-1", service="s", query="hi", latency_ms=1)
        )
        assert producer.sent[0]["topic"] == "search.events"
        assert producer.sent[0]["key"] == b"q-1"

    @pytest.mark.asyncio
    async def test_failed_event_goes_to_the_errors_topic(self, publisher, producer) -> None:
        await publisher.publish_event(
            SearchEvent(
                query_id="q-2",
                service="s",
                query="hi",
                latency_ms=1,
                status=SearchStatus.TIMEOUT,
                error_type="UpstreamTimeout",
            )  # fmt: skip
        )
        assert producer.sent[0]["topic"] == "search.errors"

    @pytest.mark.asyncio
    async def test_results_are_published_separately(self, publisher, producer) -> None:
        await publisher.publish_results(
            SearchEvent(
                query_id="q-3",
                service="s",
                query="hi",
                latency_ms=1,
                result_count=1,
                results=[SearchResult(document_id="d1", rank=1, score=0.5)],
            )  # fmt: skip
        )
        assert producer.sent[0]["topic"] == "search.results"
        assert deserialize(producer.sent[0]["value"])["results"][0]["document_id"] == "d1"

    @pytest.mark.asyncio
    async def test_a_query_with_no_results_publishes_nothing(self, publisher, producer) -> None:
        await publisher.publish_results(
            SearchEvent(query_id="q-4", service="s", query="hi", latency_ms=1)
        )
        assert producer.sent == []

    @pytest.mark.asyncio
    async def test_anomalies_are_keyed_by_service(self, publisher, producer) -> None:
        await publisher.publish_anomaly(
            AnomalyEvent(
                service="search-api",
                metric="latency_p95",
                window_start=NOW,
                window_end=NOW.replace(minute=1),
                observed=900.0,
                baseline_mean=100.0,
                baseline_stddev=10.0,
                z_score=80.0,
                sample_count=10,
            )  # fmt: skip
        )
        assert producer.sent[0]["topic"] == "search.anomalies"
        assert producer.sent[0]["key"] == b"search-api"


class TestTraceContext:
    def test_context_survives_a_kafka_round_trip(self) -> None:
        """Without this the trace breaks at the broker and debugging loses the hop."""
        # A recording provider with no exporter: real span contexts, no attempt
        # to reach a collector that is not running in a unit test.
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        trace_api.set_tracer_provider(TracerProvider())
        tracer = trace_api.get_tracer("test-service")

        with tracer.start_as_current_span("publish") as span:
            expected = span.get_span_context().trace_id
            headers = inject_trace_context()

        assert any(key == "traceparent" for key, _ in headers)
        restored = extract_trace_context(headers)
        from opentelemetry import trace

        assert trace.get_current_span(restored).get_span_context().trace_id == expected

    def test_existing_headers_are_preserved(self) -> None:
        headers = inject_trace_context([("content-type", b"application/json")])
        assert ("content-type", b"application/json") in headers

    def test_extraction_tolerates_missing_headers(self) -> None:
        assert extract_trace_context(None) is not None

    def test_disabled_sdk_still_returns_a_usable_tracer(self) -> None:
        tracer = configure_tracing("svc", Settings(otel_sdk_disabled=True))
        with tracer.start_as_current_span("noop"):
            pass


class TestBatchPublishing:
    """Awaiting each send in turn makes a 500-event batch take seconds."""

    class PipeliningProducer:
        """Mimics aiokafka: send() queues and returns a future to await later."""

        def __init__(self) -> None:
            self.queued: list[dict] = []
            self.awaited = 0
            self.order: list[str] = []

        async def send(self, topic, value=None, key=None, headers=None):
            self.queued.append({"topic": topic, "key": key})
            self.order.append("send")

            producer = self

            class Future:
                def __await__(self):
                    producer.order.append("await")
                    producer.awaited += 1
                    return iter(())

            return Future()

    @pytest.mark.asyncio
    async def test_every_send_is_queued_before_any_is_awaited(self) -> None:
        producer = self.PipeliningProducer()
        publisher = EventPublisher(producer, Settings())

        events = [
            SearchEvent(query_id=f"q-{i}", service="s", query="hi", latency_ms=1) for i in range(5)
        ]
        await publisher.publish_events(events)

        assert producer.order == ["send"] * 5 + ["await"] * 5
        assert producer.awaited == 5

    @pytest.mark.asyncio
    async def test_results_are_published_alongside_their_event(self) -> None:
        producer = self.PipeliningProducer()
        publisher = EventPublisher(producer, Settings())

        await publisher.publish_events(
            [
                SearchEvent(
                    query_id="q-1",
                    service="s",
                    query="hi",
                    latency_ms=1,
                    result_count=1,
                    results=[SearchResult(document_id="d1", rank=1, score=0.5)],
                )  # fmt: skip
            ]
        )

        topics = [item["topic"] for item in producer.queued]
        assert topics == ["search.events", "search.results"]

    @pytest.mark.asyncio
    async def test_failures_still_route_to_the_errors_topic_in_a_batch(self) -> None:
        producer = self.PipeliningProducer()
        publisher = EventPublisher(producer, Settings())

        await publisher.publish_events(
            [
                SearchEvent(query_id="q-1", service="s", query="hi", latency_ms=1),
                SearchEvent(
                    query_id="q-2",
                    service="s",
                    query="hi",
                    latency_ms=1,
                    status=SearchStatus.ERROR,
                    error_type="Boom",
                ),  # fmt: skip
            ]
        )

        assert [item["topic"] for item in producer.queued] == [
            "search.events",
            "search.errors",
        ]


class TestRedisTransportSecurity:
    """Managed Redis with transit encryption speaks TLS only."""

    def test_plaintext_by_default_for_the_local_stack(self) -> None:
        assert Settings().redis_url.startswith("redis://")

    def test_tls_changes_the_scheme(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_TLS", "true")
        assert Settings().redis_url.startswith("rediss://")

    def test_tls_and_a_password_travel_together(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_TLS", "true")
        monkeypatch.setenv("REDIS_PASSWORD", "auth-token")
        assert Settings().redis_url.startswith("rediss://:auth-token@")
