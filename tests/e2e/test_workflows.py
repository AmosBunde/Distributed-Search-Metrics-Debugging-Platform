"""End-to-end workflows, black box, through the public surface only.

These tests use the API gateway the way the dashboard and an operator do. They
never reach into ClickHouse or Kafka: if a workflow works here, it works for a
user, and if it breaks here, a user is broken regardless of what the internals
say.

Run with: make test-e2e  (after: make dev)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from stack_helpers import eventually

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def gateway(stack) -> str:
    return stack["gateway"]


def event(**overrides) -> dict:
    payload = {
        "query_id": f"e2e-{uuid.uuid4().hex[:12]}",
        "service": "e2e-search",
        "query": "end to end query",
        "latency_ms": 150.0,
        "timestamp": datetime.now(UTC).isoformat(),
        "result_count": 3,
        "relevance_score": 0.8,
    }
    payload.update(overrides)
    return payload


class TestOperatorOpensTheDashboard:
    """The first thing anyone does: load the overview and see numbers."""

    def test_traffic_appears_on_the_summary(self, gateway: str, client: httpx.Client) -> None:
        marker = f"svc-{uuid.uuid4().hex[:8]}"
        # Timestamped into a past window on purpose: the engine emits a window
        # once its watermark has moved past the end, so events landing in the
        # *current* window are correctly still open and would never show up
        # inside a test's lifetime.
        past = datetime.now(UTC) - timedelta(minutes=3)
        client.post(
            f"{gateway}/api/v1/telemetry/batch",
            json={
                "events": [
                    event(service=marker, latency_ms=100.0 + i, timestamp=past.isoformat())
                    for i in range(30)
                ]
            },
        )
        # A current event advances the watermark, which closes the past window.
        client.post(
            f"{gateway}/api/v1/telemetry/event",
            json=event(service=marker, timestamp=datetime.now(UTC).isoformat()),
        )

        services = eventually(
            lambda: client.get(f"{gateway}/api/v1/metrics/summary?minutes=60").json()["services"],
            lambda rows: any(row["service"] == marker for row in rows),
            timeout=150,
            description=f"{marker} appearing in the summary",
        )

        row = next(row for row in services if row["service"] == marker)
        assert row["queries"] >= 30
        assert row["p95"] is not None

    def test_every_dashboard_query_the_overview_makes_succeeds(
        self, gateway: str, client: httpx.Client
    ) -> None:
        """The overview fires five requests on load; all of them must answer."""
        for path in (
            "/api/v1/metrics/summary?minutes=60",
            "/api/v1/metrics/latency?minutes=60&interval=1m",
            "/api/v1/metrics/errors?minutes=60&interval=1m",
            "/api/v1/metrics/relevance?minutes=60&interval=1m",
            "/api/v1/queries/slowest?minutes=60&limit=10",
        ):
            response = client.get(f"{gateway}{path}")
            assert response.status_code == 200, f"{path} → {response.status_code}"


class TestOperatorInvestigatesASlowQuery:
    """The platform's actual purpose, driven end to end."""

    def test_from_a_slow_query_to_its_root_cause(self, gateway: str, client: httpx.Client) -> None:
        query_id = f"e2e-slow-{uuid.uuid4().hex[:8]}"
        trace_id = uuid.uuid4().hex
        now = datetime.now(UTC)

        # A slow query, and the trace that explains it.
        client.post(
            f"{gateway}/api/v1/telemetry/event",
            json=event(query_id=query_id, latency_ms=4200.0, service="e2e-search"),
        )
        spans = [
            {
                "trace_id": trace_id, "span_id": "e2e0000000000001", "parent_span_id": "",
                "query_id": query_id, "service": "e2e-search", "operation": "GET /search",
                "start_time": now.isoformat(), "duration_ms": 4200.0, "status": "ok",
                "attributes": {},
            },
            {
                "trace_id": trace_id, "span_id": "e2e0000000000002",
                "parent_span_id": "e2e0000000000001", "query_id": query_id,
                "service": "e2e-index", "operation": "fetch_shards",
                "start_time": (now + timedelta(milliseconds=80)).isoformat(),
                "duration_ms": 4000.0, "status": "error",
                "attributes": {"error.message": "shard 3 timed out"},
            },
        ]  # fmt: skip
        collector = f"{gateway.rsplit(':', 1)[0]}:8001"
        client.post(f"{collector}/api/v1/telemetry/spans", json={"spans": spans})

        # 1. The operator finds the query in the slowest list.
        slowest = eventually(
            lambda: client.get(f"{gateway}/api/v1/queries/slowest?minutes=60&limit=50").json(),
            lambda body: any(row["query_id"] == query_id for row in body["queries"]),
            timeout=150,
            description="the slow query appearing in the slowest list",
        )
        assert next(r for r in slowest["queries"] if r["query_id"] == query_id)["latency_ms"] > 4000

        # 2. They click through to the debug view.
        bundle = eventually(
            lambda: client.get(f"{gateway}/api/v1/debug/query/{query_id}").json(),
            lambda body: bool(body.get("findings")),
            timeout=120,
            description="root cause findings being produced",
        )

        assert bundle["findings"][0]["kind"] == "error_span"
        assert bundle["slowest_service"]["service"] == "e2e-index"

        # 3. And open the trace behind it.
        trace = client.get(f"{gateway}/api/v1/traces/{trace_id}").json()
        assert trace["span_count"] == 2
        assert trace["services"] == ["e2e-index", "e2e-search"]

    def test_replaying_a_query_reports_a_verdict(self, gateway: str, client: httpx.Client) -> None:
        """The replay target is unreachable here, and that is a legitimate answer."""
        query_id = f"e2e-replay-{uuid.uuid4().hex[:8]}"
        client.post(
            f"{gateway}/api/v1/telemetry/event",
            json=event(query_id=query_id, service="search-api"),
        )

        response = eventually(
            lambda: client.post(
                f"{gateway}/api/v1/debug/replay",
                json={"query_id": query_id, "target_service": "search-api"},
            ),
            lambda reply: reply.status_code == 202,
            timeout=150,
            description="the recorded run becoming replayable",
        )

        job = response.json()
        assert job["query_id"] == query_id
        assert job["status"] in {"succeeded", "failed"}
        assert job["target_service"] == "search-api"


class TestAnomalyDetection:
    """Baseline, spike, alert — the whole reason the detector exists."""

    def test_a_spike_after_a_calm_baseline_is_reported(
        self, gateway: str, client: httpx.Client
    ) -> None:
        service = f"e2e-anomaly-{uuid.uuid4().hex[:6]}"
        now = datetime.now(UTC).replace(second=0, microsecond=0)

        # Ten past windows of steady traffic: enough history for the detector to
        # have an opinion, with enough variance that its baseline is usable.
        for index in range(10):
            window = now - timedelta(minutes=20 - index)
            client.post(
                f"{gateway}/api/v1/telemetry/batch",
                json={
                    "events": [
                        event(
                            service=service,
                            latency_ms=100.0 + (position % 7) + index,
                            timestamp=(window + timedelta(seconds=position % 50)).isoformat(),
                        )
                        for position in range(25)
                    ]
                },
            )

        # Then a window that is wildly out of character.
        spike_window = now - timedelta(minutes=9)
        client.post(
            f"{gateway}/api/v1/telemetry/batch",
            json={
                "events": [
                    event(
                        service=service,
                        latency_ms=9000.0,
                        timestamp=(spike_window + timedelta(seconds=position % 50)).isoformat(),
                    )
                    for position in range(25)
                ]
            },
        )
        # And a recent event to push the watermark past the spike window.
        client.post(
            f"{gateway}/api/v1/telemetry/event",
            json=event(service=service, timestamp=now.isoformat()),
        )

        anomalies = eventually(
            lambda: client.get(f"{gateway}/api/v1/anomalies?minutes=120&service={service}").json()[
                "anomalies"
            ],
            bool,
            timeout=180,
            description="the spike being reported as an anomaly",
        )

        latency_anomaly = next(a for a in anomalies if a["metric"].startswith("latency"))
        assert latency_anomaly["z_score"] > 3
        assert latency_anomaly["observed"] > 5000
        assert latency_anomaly["severity"] in {"warning", "critical"}


class TestApiContract:
    def test_the_documented_endpoints_are_all_served(
        self, gateway: str, client: httpx.Client
    ) -> None:
        """The README's API table is a promise to adopters."""
        schema = client.get(f"{gateway}/openapi.json").json()

        for method, path in [
            ("post", "/api/v1/telemetry/event"),
            ("post", "/api/v1/telemetry/batch"),
            ("get", "/api/v1/metrics/latency"),
            ("get", "/api/v1/metrics/relevance"),
            ("get", "/api/v1/metrics/errors"),
            ("get", "/api/v1/metrics/summary"),
            ("get", "/api/v1/anomalies"),
            ("get", "/api/v1/traces/{trace_id}"),
            ("get", "/api/v1/debug/query/{query_id}"),
            ("post", "/api/v1/debug/replay"),
        ]:
            assert path in schema["paths"], f"{path} is documented but not served"
            assert method in schema["paths"][path]

    def test_bad_input_is_rejected_consistently(self, gateway: str, client: httpx.Client) -> None:
        assert client.get(f"{gateway}/api/v1/metrics/latency?minutes=0").status_code == 422
        assert client.get(f"{gateway}/api/v1/metrics/latency?interval=7s").status_code == 422
        assert client.get(f"{gateway}/api/v1/anomalies?severity=nope").status_code == 422
        assert client.post(f"{gateway}/api/v1/telemetry/event", json={}).status_code == 422

    def test_an_unknown_identifier_is_a_404_not_an_error(
        self, gateway: str, client: httpx.Client
    ) -> None:
        assert client.get(f"{gateway}/api/v1/traces/nonexistent").status_code == 404
        assert client.get(f"{gateway}/api/v1/debug/query/nonexistent").status_code == 404


class TestTheStackIsHealthy:
    def test_every_service_reports_healthy(self, stack, client: httpx.Client) -> None:
        for name, url in stack.items():
            body = client.get(f"{url}/health").json()
            assert body["status"] in {"ok", "degraded"}, f"{name} reported {body}"

    def test_the_dashboard_is_served_and_proxies_the_api(self, client: httpx.Client) -> None:
        from stack_helpers import _env

        dashboard = f"http://localhost:{_env('DASHBOARD_PORT', '3000')}"
        try:
            page = client.get(f"{dashboard}/")
        except httpx.HTTPError:
            pytest.skip("dashboard is not running")

        assert page.status_code == 200
        assert '<div id="root">' in page.text
        # nginx proxies /api, which is what removes CORS from the picture.
        assert client.get(f"{dashboard}/api/v1/metrics/summary?minutes=60").status_code == 200
