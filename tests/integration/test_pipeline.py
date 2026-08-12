"""Integration tests: each hop of the pipeline against the real stack.

Unit tests prove each service is right on its own. These prove the joins hold —
the parts the unit tests deliberately fake: a real broker, real SQL against a
real schema, real serialisation across a network.

Run with: make test-integration  (after: make dev)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from stack_helpers import clickhouse, clickhouse_count, eventually

pytestmark = pytest.mark.integration


def make_event(**overrides) -> dict:
    payload = {
        "query_id": f"it-{uuid.uuid4().hex[:12]}",
        "service": "integration-test",
        "query": "integration test query",
        "latency_ms": 123.4,
        "timestamp": datetime.now(UTC).isoformat(),
        "result_count": 2,
        "relevance_score": 0.77,
    }
    payload.update(overrides)
    return payload


class TestCollectorToKafkaToClickHouse:
    """The ingest path, end to end, one event at a time."""

    def test_an_accepted_event_reaches_clickhouse(self, stack, client: httpx.Client) -> None:
        event = make_event()

        response = client.post(f"{stack['collector']}/api/v1/telemetry/event", json=event)
        assert response.status_code == 202
        assert response.json()["accepted"] == 1

        # Ingest → Kafka → engine → batched insert. Poll rather than sleep.
        eventually(
            lambda: clickhouse_count("events", f"query_id = '{event['query_id']}'"),
            lambda count: count >= 1,
            timeout=90,
            description="the event reaching ClickHouse",
        )

    def test_the_stored_row_matches_what_was_sent(self, stack, client: httpx.Client) -> None:
        event = make_event(latency_ms=456.75, result_count=7, relevance_score=0.5, cache_hit=True)
        client.post(f"{stack['collector']}/api/v1/telemetry/event", json=event)

        rows = eventually(
            lambda: clickhouse(
                "SELECT latency_ms, result_count, relevance_score, cache_hit, service "
                f"FROM events WHERE query_id = '{event['query_id']}'"
            ),
            bool,
            timeout=90,
            description="the row appearing",
        )

        latency, results, relevance, cache_hit, service = rows[0]
        assert float(latency) == pytest.approx(456.75)
        assert int(results) == 7
        assert float(relevance) == pytest.approx(0.5)
        assert cache_hit == "1"
        assert service == "integration-test"

    def test_a_failed_query_is_stored_with_its_error(self, stack, client: httpx.Client) -> None:
        """Failures travel a different topic; they must still land in the table."""
        event = make_event(
            status="timeout", error_type="ShardTimeout", error_message="shard 3 timed out",
            result_count=0, relevance_score=None,
        )  # fmt: skip
        assert (
            client.post(f"{stack['collector']}/api/v1/telemetry/event", json=event).status_code
            == 202
        )

        rows = eventually(
            lambda: clickhouse(
                f"SELECT status, error_type FROM events WHERE query_id = '{event['query_id']}'"
            ),
            bool,
            timeout=90,
            description="the failed query being stored",
        )
        assert rows[0] == ["timeout", "ShardTimeout"]

    def test_a_batch_lands_completely(self, stack, client: httpx.Client) -> None:
        marker = f"batch-{uuid.uuid4().hex[:8]}"
        events = [make_event(query_id=f"{marker}-{index}") for index in range(25)]

        response = client.post(
            f"{stack['collector']}/api/v1/telemetry/batch", json={"events": events}
        )
        assert response.json()["accepted"] == 25

        eventually(
            lambda: clickhouse_count("events", f"query_id LIKE '{marker}%'"),
            lambda count: count >= 25,
            timeout=90,
            description="the whole batch arriving",
        )

    def test_an_invalid_event_never_reaches_storage(self, stack, client: httpx.Client) -> None:
        """Validation at the edge is only worth anything if it actually stops it."""
        event = make_event(latency_ms=-1)

        response = client.post(f"{stack['collector']}/api/v1/telemetry/event", json=event)
        assert response.status_code == 422

        assert clickhouse_count("events", f"query_id = '{event['query_id']}'") == 0


class TestResultsAndSpans:
    def test_per_document_results_are_stored(self, stack, client: httpx.Client) -> None:
        event = make_event(
            result_count=3,
            results=[
                {"document_id": f"doc-{index}", "rank": index, "score": 0.9 - index / 10}
                for index in range(1, 4)
            ],
        )
        client.post(f"{stack['collector']}/api/v1/telemetry/event", json=event)

        eventually(
            lambda: clickhouse_count("query_results", f"query_id = '{event['query_id']}'"),
            lambda count: count >= 3,
            timeout=90,
            description="query results being stored",
        )

    def test_spans_reach_the_trace_store(self, stack, client: httpx.Client) -> None:
        trace_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        spans = [
            {
                "trace_id": trace_id,
                "span_id": "aaaa000000000001",
                "parent_span_id": "",
                "query_id": "it-span-root",
                "service": "integration-test",
                "operation": "GET /search",
                "start_time": now.isoformat(),
                "duration_ms": 500.0,
                "status": "ok",
                "attributes": {"cache.hit": "false"},
            },
            {
                "trace_id": trace_id,
                "span_id": "aaaa000000000002",
                "parent_span_id": "aaaa000000000001",
                "query_id": "it-span-root",
                "service": "integration-child",
                "operation": "fetch",
                "start_time": (now + timedelta(milliseconds=50)).isoformat(),
                "duration_ms": 400.0,
                "status": "error",
                "attributes": {"error.message": "boom"},
            },
        ]

        response = client.post(
            f"{stack['collector']}/api/v1/telemetry/spans", json={"spans": spans}
        )
        assert response.status_code == 202
        assert response.json()["accepted"] == 2

        eventually(
            lambda: clickhouse_count("spans", f"trace_id = '{trace_id}'"),
            lambda count: count >= 2,
            timeout=90,
            description="spans reaching the trace store",
        )

    def test_a_malformed_span_is_rejected_without_losing_the_rest(
        self, stack, client: httpx.Client
    ) -> None:
        trace_id = uuid.uuid4().hex
        response = client.post(
            f"{stack['collector']}/api/v1/telemetry/spans",
            json={
                "spans": [
                    {
                        "trace_id": trace_id,
                        "span_id": "bbbb000000000001",
                        "service": "integration-test",
                        "operation": "ok-span",
                        "start_time": datetime.now(UTC).isoformat(),
                        "duration_ms": 10.0,
                    },
                    {"trace_id": trace_id, "span_id": "broken"},
                ]
            },
        )

        body = response.json()
        assert (body["accepted"], body["rejected"]) == (1, 1)
        assert "event 1" in body["errors"][0]


class TestMetricsEngine:
    def test_windows_close_into_rollups(self, stack, client: httpx.Client) -> None:
        """A closed window must produce exactly one row per service."""
        service = f"rollup-{uuid.uuid4().hex[:8]}"
        window = datetime.now(UTC) - timedelta(minutes=5)

        client.post(
            f"{stack['collector']}/api/v1/telemetry/batch",
            json={
                "events": [
                    make_event(
                        service=service,
                        latency_ms=100.0 + index,
                        timestamp=window.isoformat(),
                    )
                    for index in range(20)
                ]
            },
        )
        # A later event advances the watermark so the earlier window closes.
        client.post(
            f"{stack['collector']}/api/v1/telemetry/event",
            json=make_event(service=service, timestamp=datetime.now(UTC).isoformat()),
        )

        rows = eventually(
            lambda: clickhouse(
                "SELECT query_count, latency_p50 FROM metric_rollups "
                f"WHERE service = '{service}' ORDER BY window_start"
            ),
            bool,
            timeout=120,
            description="a window closing into a rollup",
        )

        assert int(rows[0][0]) == 20
        assert 100 <= float(rows[0][1]) <= 120


class TestGateway:
    def test_metrics_endpoints_answer_from_clickhouse(self, stack, client: httpx.Client) -> None:
        for path in (
            "/api/v1/metrics/summary?minutes=1440",
            "/api/v1/metrics/latency?minutes=1440&interval=15m",
            "/api/v1/metrics/errors?minutes=1440&interval=15m",
            "/api/v1/metrics/relevance?minutes=1440&interval=15m",
            "/api/v1/anomalies?minutes=1440",
            "/api/v1/queries/slowest?minutes=1440&limit=5",
        ):
            response = client.get(f"{stack['gateway']}{path}")
            assert response.status_code == 200, f"{path} returned {response.status_code}"

    def test_the_summary_reflects_ingested_traffic(self, stack, client: httpx.Client) -> None:
        client.post(
            f"{stack['collector']}/api/v1/telemetry/batch",
            json={"events": [make_event() for _ in range(10)]},
        )

        totals = eventually(
            lambda: client.get(f"{stack['gateway']}/api/v1/metrics/summary?minutes=1440").json()[
                "totals"
            ],
            lambda totals: (totals.get("queries") or 0) > 0,
            timeout=120,
            description="the summary reporting traffic",
        )
        assert isinstance(totals["queries"], int), "counts must be numbers, not strings"

    def test_the_gateway_proxies_the_debug_service(self, stack, client: httpx.Client) -> None:
        direct = client.get(f"{stack['debug']}/api/v1/traces/does-not-exist")
        proxied = client.get(f"{stack['gateway']}/api/v1/traces/does-not-exist")

        assert direct.status_code == proxied.status_code == 404

    def test_ingest_through_the_gateway_reaches_the_collector(
        self, stack, client: httpx.Client
    ) -> None:
        event = make_event()
        response = client.post(f"{stack['gateway']}/api/v1/telemetry/event", json=event)

        assert response.status_code == 202
        eventually(
            lambda: clickhouse_count("events", f"query_id = '{event['query_id']}'"),
            lambda count: count >= 1,
            timeout=90,
            description="an event ingested through the gateway",
        )


class TestDebugService:
    def test_a_trace_is_assembled_from_stored_spans(self, stack, client: httpx.Client) -> None:
        trace_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        query_id = f"debug-{uuid.uuid4().hex[:8]}"

        client.post(
            f"{stack['collector']}/api/v1/telemetry/spans",
            json={
                "spans": [
                    {
                        "trace_id": trace_id, "span_id": "cccc000000000001", "parent_span_id": "",
                        "query_id": query_id, "service": "search-api", "operation": "GET /search",
                        "start_time": now.isoformat(), "duration_ms": 900.0, "status": "ok",
                        "attributes": {},
                    },
                    {
                        "trace_id": trace_id, "span_id": "cccc000000000002",
                        "parent_span_id": "cccc000000000001", "query_id": query_id,
                        "service": "index-service", "operation": "fetch",
                        "start_time": (now + timedelta(milliseconds=100)).isoformat(),
                        "duration_ms": 750.0, "status": "error",
                        "attributes": {"error.message": "shard timeout"},
                    },
                ]
            },
        )  # fmt: skip

        trace = eventually(
            lambda: client.get(f"{stack['debug']}/api/v1/traces/{trace_id}"),
            lambda response: response.status_code == 200,
            timeout=90,
            description="the trace becoming assemblable",
        ).json()

        assert trace["span_count"] == 2
        assert trace["roots"][0]["children"][0]["service"] == "index-service"
        assert trace["critical_path"][0]["service"] == "search-api"

    def test_root_cause_analysis_finds_the_failing_span(self, stack, client: httpx.Client) -> None:
        query_id = f"rca-{uuid.uuid4().hex[:8]}"
        trace_id = uuid.uuid4().hex
        now = datetime.now(UTC)

        client.post(
            f"{stack['collector']}/api/v1/telemetry/event",
            json=make_event(query_id=query_id, latency_ms=1500.0, service="search-api"),
        )
        client.post(
            f"{stack['collector']}/api/v1/telemetry/spans",
            json={
                "spans": [
                    {
                        "trace_id": trace_id, "span_id": "dddd000000000001", "parent_span_id": "",
                        "query_id": query_id, "service": "search-api", "operation": "GET /search",
                        "start_time": now.isoformat(), "duration_ms": 1500.0, "status": "ok",
                        "attributes": {},
                    },
                    {
                        "trace_id": trace_id, "span_id": "dddd000000000002",
                        "parent_span_id": "dddd000000000001", "query_id": query_id,
                        "service": "index-service", "operation": "fetch",
                        "start_time": (now + timedelta(milliseconds=50)).isoformat(),
                        "duration_ms": 1400.0, "status": "error",
                        "attributes": {"error.message": "shard timeout"},
                    },
                ]
            },
        )  # fmt: skip

        bundle = eventually(
            lambda: client.get(f"{stack['debug']}/api/v1/debug/query/{query_id}").json(),
            lambda body: bool(body.get("findings")),
            timeout=90,
            description="root cause analysis producing findings",
        )

        assert bundle["findings"][0]["kind"] == "error_span"
        assert bundle["findings"][0]["service"] == "index-service"
        assert "index-service" in bundle["summary"]

    def test_replay_refuses_a_target_off_the_allowlist(self, stack, client: httpx.Client) -> None:
        """The SSRF guard has to hold on the running service, not just in a unit test."""
        response = client.post(
            f"{stack['debug']}/api/v1/debug/replay",
            json={"query_id": "anything", "target_service": "internal-admin"},
        )
        assert response.status_code in {400, 404}
        if response.status_code == 400:
            assert "not an allowed replay target" in response.json()["detail"]

    def test_replay_rejects_a_url_shaped_target(self, stack, client: httpx.Client) -> None:
        response = client.post(
            f"{stack['debug']}/api/v1/debug/replay",
            json={"query_id": "anything", "target_service": "http://169.254.169.254/latest"},
        )
        assert response.status_code == 422


class TestObservability:
    @pytest.mark.parametrize(
        ("service", "metric"),
        [
            ("collector", "collector_events_ingested_total"),
            ("engine", "engine_events_processed_total"),
            ("gateway", "gateway_requests_total"),
        ],
    )
    def test_services_expose_their_prometheus_metrics(
        self, stack, client: httpx.Client, service: str, metric: str
    ) -> None:
        urls = {
            "collector": stack["collector"],
            "engine": stack["engine"],
            "gateway": stack["gateway"],
        }
        response = client.get(f"{urls[service]}/metrics")

        assert response.status_code == 200
        assert metric in response.text
