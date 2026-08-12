"""Telemetry collector: ingest logic, rate limiting and the HTTP surface.

The service is exercised through its real ASGI app with the Kafka producer and
Redis replaced by fakes — no infrastructure, but every layer the request
actually passes through is the real one.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from search_metrics_common import SearchEvent, SearchStatus
from services_path import add_service_to_path

add_service_to_path("telemetry-collector")

from collector.ingest import enrich, ingest_events  # noqa: E402
from collector.main import app  # noqa: E402
from collector.rate_limit import (  # noqa: E402
    InMemoryTokenBucket,
    NullRateLimiter,
    RedisTokenBucket,
    build_rate_limiter,
)

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def valid_event(**overrides) -> dict:
    payload = {
        "query_id": "q-1",
        "service": "search-api",
        "query": "distributed tracing",
        "latency_ms": 42.0,
        "timestamp": NOW.isoformat(),
        "result_count": 1,
        "relevance_score": 0.9,
    }
    payload.update(overrides)
    return payload


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[SearchEvent] = []
        self.results: list[SearchEvent] = []

    async def publish_event(self, event: SearchEvent) -> None:
        self.events.append(event)

    async def publish_results(self, event: SearchEvent) -> None:
        if event.results:
            self.results.append(event)


class TestEnrichment:
    def test_receive_time_is_recorded_separately_from_the_caller_timestamp(self) -> None:
        """A client with a skewed clock must be visible, not silently trusted."""
        event = SearchEvent.model_validate(valid_event())
        enriched = enrich(event, received_at=NOW + timedelta(seconds=5))

        assert enriched.timestamp == NOW
        assert enriched.metadata["received_at"] == (NOW + timedelta(seconds=5)).isoformat()

    def test_caller_metadata_is_preserved(self) -> None:
        event = SearchEvent.model_validate(valid_event(metadata={"region": "eu-west-1"}))
        assert enrich(event).metadata["region"] == "eu-west-1"

    def test_a_supplied_trace_id_is_not_overwritten(self) -> None:
        event = SearchEvent.model_validate(valid_event(trace_id="a" * 32))
        assert enrich(event).trace_id == "a" * 32


class TestIngest:
    @pytest.mark.asyncio
    async def test_valid_events_are_published(self) -> None:
        publisher = RecordingPublisher()
        result = await ingest_events([valid_event(), valid_event(query_id="q-2")], publisher)

        assert (result.accepted, result.rejected) == (2, 0)
        assert [event.query_id for event in publisher.events] == ["q-1", "q-2"]

    @pytest.mark.asyncio
    async def test_one_bad_event_does_not_reject_the_batch(self) -> None:
        """The whole point of partial success: 999 good events must still land."""
        publisher = RecordingPublisher()
        result = await ingest_events(
            [
                valid_event(),
                valid_event(query_id="q-2", latency_ms=-5),
                valid_event(query_id="q-3"),
            ],
            publisher,
        )

        assert (result.accepted, result.rejected) == (2, 1)
        assert [event.query_id for event in publisher.events] == ["q-1", "q-3"]

    @pytest.mark.asyncio
    async def test_rejection_names_the_field_at_fault(self) -> None:
        publisher = RecordingPublisher()
        result = await ingest_events([valid_event(latency_ms=-5)], publisher)

        assert result.errors[0].startswith("event 0: latency_ms")

    @pytest.mark.asyncio
    async def test_failed_searches_are_accepted_and_routed_to_errors(self) -> None:
        publisher = RecordingPublisher()
        result = await ingest_events(
            [valid_event(status="timeout", error_type="UpstreamTimeout")], publisher
        )

        assert result.accepted == 1
        assert publisher.events[0].status is SearchStatus.TIMEOUT
        assert publisher.events[0].is_failure

    @pytest.mark.asyncio
    async def test_results_are_published_only_when_present(self) -> None:
        publisher = RecordingPublisher()
        await ingest_events(
            [
                valid_event(),
                valid_event(
                    query_id="q-2",
                    result_count=1,
                    results=[{"document_id": "d1", "rank": 1, "score": 0.7}],
                ),
            ],
            publisher,
        )

        assert len(publisher.events) == 2
        assert len(publisher.results) == 1

    @pytest.mark.asyncio
    async def test_empty_input_is_a_no_op(self) -> None:
        result = await ingest_events([], RecordingPublisher())
        assert (result.accepted, result.rejected) == (0, 0)


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_requests_within_the_burst_are_allowed(self) -> None:
        bucket = InMemoryTokenBucket(rate_per_second=1, capacity=3, clock=lambda: 0.0)
        for _ in range(3):
            assert (await bucket.check("client")).allowed

    @pytest.mark.asyncio
    async def test_exhausting_the_bucket_rejects_with_a_retry_hint(self) -> None:
        bucket = InMemoryTokenBucket(rate_per_second=1, capacity=1, clock=lambda: 0.0)
        assert (await bucket.check("client")).allowed

        decision = await bucket.check("client")
        assert not decision.allowed
        assert decision.retry_after_seconds >= 1

    @pytest.mark.asyncio
    async def test_the_bucket_refills_over_time(self) -> None:
        now = [0.0]
        bucket = InMemoryTokenBucket(rate_per_second=10, capacity=1, clock=lambda: now[0])

        assert (await bucket.check("client")).allowed
        assert not (await bucket.check("client")).allowed

        now[0] = 0.5  # five tokens' worth of time
        assert (await bucket.check("client")).allowed

    @pytest.mark.asyncio
    async def test_clients_have_independent_buckets(self) -> None:
        bucket = InMemoryTokenBucket(rate_per_second=1, capacity=1, clock=lambda: 0.0)
        assert (await bucket.check("noisy")).allowed
        assert not (await bucket.check("noisy")).allowed
        assert (await bucket.check("quiet")).allowed

    @pytest.mark.asyncio
    async def test_a_redis_outage_fails_open(self) -> None:
        """Losing the limiter must not become losing telemetry."""

        class BrokenRedis:
            def register_script(self, _):
                async def script(keys, args):
                    raise ConnectionError("redis is down")

                return script

        limiter = RedisTokenBucket(BrokenRedis(), rate_per_second=1, capacity=1)
        assert (await limiter.check("client")).allowed


class TestLimiterSelection:
    def test_disabled_gives_a_null_limiter(self) -> None:
        settings = type("S", (), {"rate_limit_enabled": False})()
        assert isinstance(build_rate_limiter(settings), NullRateLimiter)

    def test_without_redis_it_falls_back_in_memory(self) -> None:
        settings = type(
            "S",
            (),
            {
                "rate_limit_enabled": True,
                "rate_limit_requests_per_minute": 600,
                "rate_limit_burst": 60,
            },
        )()
        assert isinstance(build_rate_limiter(settings), InMemoryTokenBucket)


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """The real app, with Kafka and Redis replaced and the lifespan skipped."""
    publisher = RecordingPublisher()
    app.state.publisher = publisher
    app.state.redis = None
    app.state.rate_limiter = NullRateLimiter()

    with TestClient(app) as test_client:
        test_client.publisher = publisher  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture(autouse=True)
def skip_lifespan(monkeypatch) -> None:
    """The lifespan would try to reach a broker; state is injected instead."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def noop(app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop)


class TestHttpSurface:
    def test_single_event_is_accepted(self, client: TestClient) -> None:
        response = client.post("/api/v1/telemetry/event", json=valid_event())

        assert response.status_code == 202
        assert response.json()["accepted"] == 1
        assert client.publisher.events[0].query_id == "q-1"  # type: ignore[attr-defined]

    def test_invalid_event_is_rejected_with_422(self, client: TestClient) -> None:
        response = client.post("/api/v1/telemetry/event", json=valid_event(latency_ms=-1))
        assert response.status_code == 422

    def test_batch_reports_partial_success(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/telemetry/batch",
            json={"events": [valid_event(), valid_event(query_id="q-2", relevance_score=7)]},
        )

        body = response.json()
        assert response.status_code == 202
        assert (body["accepted"], body["rejected"]) == (1, 1)
        assert "relevance_score" in body["errors"][0]

    def test_oversized_batch_is_rejected_with_413(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/telemetry/batch",
            json={"events": [valid_event() for _ in range(501)]},
        )

        assert response.status_code == 413
        assert response.json()["limit"] == 500

    @pytest.mark.parametrize("body", [{}, {"events": []}, {"events": "nope"}])
    def test_malformed_batch_body_is_rejected_with_400(self, client: TestClient, body) -> None:
        assert client.post("/api/v1/telemetry/batch", json=body).status_code == 400

    def test_rate_limited_requests_get_429_and_retry_after(self, client: TestClient) -> None:
        app.state.rate_limiter = InMemoryTokenBucket(
            rate_per_second=1, capacity=1, clock=lambda: 0.0
        )

        assert client.post("/api/v1/telemetry/event", json=valid_event()).status_code == 202
        response = client.post("/api/v1/telemetry/event", json=valid_event())

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 1

    def test_clients_are_limited_independently(self, client: TestClient) -> None:
        app.state.rate_limiter = InMemoryTokenBucket(
            rate_per_second=1, capacity=1, clock=lambda: 0.0
        )
        headers_a = {"X-Client-Id": "service-a"}
        headers_b = {"X-Client-Id": "service-b"}

        assert (
            client.post(
                "/api/v1/telemetry/event", json=valid_event(), headers=headers_a
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/api/v1/telemetry/event", json=valid_event(), headers=headers_a
            ).status_code
            == 429
        )
        assert (
            client.post(
                "/api/v1/telemetry/event", json=valid_event(), headers=headers_b
            ).status_code
            == 202
        )

    def test_health_reports_dependencies(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["kafka"] is True

    def test_metrics_are_exposed_for_prometheus(self, client: TestClient) -> None:
        client.post("/api/v1/telemetry/event", json=valid_event())
        body = client.get("/metrics").text
        assert "collector_events_ingested_total" in body

    def test_openapi_documents_both_ingest_routes(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/telemetry/event" in paths
        assert "/api/v1/telemetry/batch" in paths
