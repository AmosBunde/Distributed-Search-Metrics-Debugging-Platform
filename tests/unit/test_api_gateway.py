"""API gateway: query building, caching, parameter validation and proxying."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from services_path import add_service_to_path

add_service_to_path("api-gateway")

from gateway.cache import TTL_SECONDS, MetricsCache, cache_key  # noqa: E402
from gateway.queries import MetricsQueries, QueryError, TimeRange  # noqa: E402

BASE = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
WINDOW = TimeRange(start=BASE, end=BASE + timedelta(hours=1))


def queries_returning(rows: list[dict], capture: dict | None = None) -> MetricsQueries:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["sql"] = request.content.decode()
            capture["params"] = dict(request.url.params)
        return httpx.Response(200, text="\n".join(__import__("json").dumps(r) for r in rows))

    return MetricsQueries(
        url="http://clickhouse:8123",
        database="search_metrics",
        user="search",
        password="changeme",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestTimeRange:
    def test_lookback_ends_now(self) -> None:
        window = TimeRange.last(60)
        assert (window.end - window.start) == timedelta(minutes=60)

    def test_parameters_are_clickhouse_datetimes(self) -> None:
        assert WINDOW.as_params() == {
            "start": "2026-08-12 10:00:00",
            "end": "2026-08-12 11:00:00",
        }


class TestQueries:
    @pytest.mark.asyncio
    async def test_values_are_parameterised_never_interpolated(self) -> None:
        """A service name from a query string must not reach the SQL text."""
        capture: dict = {}
        await queries_returning([], capture).latency(WINDOW, service="'; DROP TABLE events--")

        assert "DROP TABLE" not in capture["sql"]
        assert capture["params"]["param_service"] == "'; DROP TABLE events--"

    @pytest.mark.asyncio
    async def test_the_service_filter_is_omitted_when_unset(self) -> None:
        capture: dict = {}
        await queries_returning([], capture).latency(WINDOW)

        assert "service = {service:String}" not in capture["sql"]
        assert "param_service" not in capture["params"]

    @pytest.mark.asyncio
    async def test_an_unknown_interval_is_rejected_not_substituted(self) -> None:
        """Silently falling back would give the caller data they did not ask for."""
        with pytest.raises(ValueError, match="interval must be one of"):
            await queries_returning([]).latency(WINDOW, interval="1 second; DROP TABLE events")

    @pytest.mark.asyncio
    async def test_each_interval_maps_to_a_clickhouse_bucket(self) -> None:
        for interval, expected in [
            ("1m", "toStartOfMinute"),
            ("5m", "toStartOfFiveMinute"),
            ("1h", "toStartOfHour"),
            ("1d", "toStartOfDay"),
        ]:
            capture: dict = {}
            await queries_returning([], capture).latency(WINDOW, interval=interval)
            assert expected in capture["sql"]

    @pytest.mark.asyncio
    async def test_rows_are_decoded_from_json_each_row(self) -> None:
        rows = await queries_returning(
            [{"bucket": "2026-08-12 10:00:00", "service": "search-api", "p95": 120.0}]
        ).latency(WINDOW)

        assert rows[0]["p95"] == 120.0

    @pytest.mark.asyncio
    async def test_summary_includes_the_open_anomaly_count(self) -> None:
        summary = await queries_returning([{"queries": 100, "open_anomalies": 0}]).summary(WINDOW)
        assert "open_anomalies" in summary

    @pytest.mark.asyncio
    async def test_an_empty_result_set_is_not_an_error(self) -> None:
        assert await queries_returning([]).latency(WINDOW) == []

    @pytest.mark.asyncio
    async def test_anomaly_limit_is_clamped(self) -> None:
        capture: dict = {}
        await queries_returning([], capture).anomalies(WINDOW, limit=99_999)
        assert capture["params"]["param_limit"] == "1000"

    @pytest.mark.asyncio
    async def test_a_negative_offset_is_clamped_to_zero(self) -> None:
        capture: dict = {}
        await queries_returning([], capture).anomalies(WINDOW, offset=-5)
        assert capture["params"]["param_offset"] == "0"

    @pytest.mark.asyncio
    async def test_a_clickhouse_error_becomes_a_query_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="Syntax error near FROM")

        broken = MetricsQueries(
            url="http://clickhouse:8123",
            database="db",
            user="u",
            password="p",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(QueryError, match="rejected the query"):
            await broken.latency(WINDOW)

    @pytest.mark.asyncio
    async def test_an_unreachable_clickhouse_becomes_a_query_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        broken = MetricsQueries(
            url="http://clickhouse:8123",
            database="db",
            user="u",
            password="p",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(QueryError, match="unreachable"):
            await broken.latency(WINDOW)


class FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.broken = broken
        self.writes = 0

    async def get(self, key: str):
        if self.broken:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if self.broken:
            raise ConnectionError("redis down")
        self.writes += 1
        self.store[key] = value


class TestCache:
    def test_keys_differ_by_parameters(self) -> None:
        assert cache_key("latency", minutes=60) != cache_key("latency", minutes=120)
        assert cache_key("latency", minutes=60) != cache_key("errors", minutes=60)

    def test_keys_are_stable_and_bounded(self) -> None:
        """Hashing keeps a long time range or odd service name from bloating keys."""
        first = cache_key("latency", service="a" * 500, minutes=60)
        second = cache_key("latency", service="a" * 500, minutes=60)
        assert first == second
        assert len(first) < 64

    def test_anomalies_have_the_shortest_ttl(self) -> None:
        """A stale anomaly feed is the one thing on-call would actually mind."""
        assert TTL_SECONDS["anomalies"] < TTL_SECONDS["relevance"]

    @pytest.mark.asyncio
    async def test_a_miss_loads_and_stores(self) -> None:
        redis = FakeRedis()
        cache = MetricsCache(redis)
        calls = {"n": 0}

        async def loader():
            calls["n"] += 1
            return [{"p95": 1}]

        assert await cache.get_or_set("latency", loader, minutes=60) == [{"p95": 1}]
        assert (calls["n"], redis.writes, cache.misses) == (1, 1, 1)

    @pytest.mark.asyncio
    async def test_a_hit_does_not_call_the_loader(self) -> None:
        cache = MetricsCache(FakeRedis())

        async def loader():
            return [{"p95": 1}]

        await cache.get_or_set("latency", loader, minutes=60)

        async def fail():
            raise AssertionError("loader must not run on a cache hit")

        assert await cache.get_or_set("latency", fail, minutes=60) == [{"p95": 1}]
        assert cache.hits == 1

    @pytest.mark.asyncio
    async def test_different_parameters_do_not_share_an_entry(self) -> None:
        cache = MetricsCache(FakeRedis())

        async def one():
            return "sixty"

        async def two():
            return "one-twenty"

        assert await cache.get_or_set("latency", one, minutes=60) == "sixty"
        assert await cache.get_or_set("latency", two, minutes=120) == "one-twenty"

    @pytest.mark.asyncio
    async def test_a_redis_outage_degrades_to_direct_queries(self) -> None:
        """Redis down should make the dashboard slower, not broken."""
        cache = MetricsCache(FakeRedis(broken=True))

        async def loader():
            return [{"p95": 2}]

        assert await cache.get_or_set("latency", loader, minutes=60) == [{"p95": 2}]

    @pytest.mark.asyncio
    async def test_without_redis_every_call_reaches_the_loader(self) -> None:
        cache = MetricsCache(None)
        calls = {"n": 0}

        async def loader():
            calls["n"] += 1
            return 1

        await cache.get_or_set("latency", loader, minutes=60)
        await cache.get_or_set("latency", loader, minutes=60)

        assert calls["n"] == 2
        assert not cache.enabled

    @pytest.mark.asyncio
    async def test_hit_rate_is_reported(self) -> None:
        cache = MetricsCache(FakeRedis())

        async def loader():
            return 1

        await cache.get_or_set("summary", loader, minutes=60)  # miss
        await cache.get_or_set("summary", loader, minutes=60)  # hit
        assert cache.hit_rate == 0.5


@pytest.fixture(autouse=True)
def skip_lifespan(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from gateway.main import app

    @asynccontextmanager
    async def noop(app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop)


def client_with(rows: list[dict], upstream=None) -> TestClient:
    from gateway.main import app

    app.state.queries = queries_returning(rows)
    app.state.cache = MetricsCache(None)
    app.state.http = upstream
    return TestClient(app)


ROW = {"bucket": "2026-08-12 10:00:00", "service": "search-api", "p95": 120.0, "queries": 10}


class TestEndpoints:
    def test_latency_returns_a_series_and_its_window(self) -> None:
        with client_with([ROW]) as client:
            body = client.get("/api/v1/metrics/latency?minutes=30").json()

        assert body["series"][0]["p95"] == 120.0
        assert body["interval"] == "1m"
        assert "approximation" in body["note"]

    def test_the_percentile_caveat_is_stated_in_the_response(self) -> None:
        """Averaged percentiles are an approximation; the API says so."""
        with client_with([ROW]) as client:
            assert "approximation" in client.get("/api/v1/metrics/latency").json()["note"]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/metrics/latency",
            "/api/v1/metrics/relevance",
            "/api/v1/metrics/errors",
            "/api/v1/metrics/summary",
            "/api/v1/anomalies",
            "/api/v1/queries/slowest",
        ],
    )
    def test_every_metrics_endpoint_answers(self, path: str) -> None:
        with client_with([ROW]) as client:
            assert client.get(path).status_code == 200

    def test_an_empty_range_returns_an_empty_series_not_an_error(self) -> None:
        with client_with([]) as client:
            body = client.get("/api/v1/metrics/latency?minutes=1").json()
        assert body["series"] == []

    def test_summary_includes_totals_and_per_service_rows(self) -> None:
        with client_with([{"queries": 500, "open_anomalies": 2}]) as client:
            body = client.get("/api/v1/metrics/summary").json()

        assert body["totals"]["queries"] == 500
        assert isinstance(body["services"], list)

    @pytest.mark.parametrize("minutes", ["0", "-5", "999999", "abc"])
    def test_an_invalid_window_is_rejected(self, minutes: str) -> None:
        with client_with([ROW]) as client:
            assert client.get(f"/api/v1/metrics/latency?minutes={minutes}").status_code == 422

    def test_an_invalid_interval_is_rejected(self) -> None:
        with client_with([ROW]) as client:
            response = client.get("/api/v1/metrics/latency?interval=7s")
        assert response.status_code == 422
        assert "interval must be one of" in response.json()["detail"]

    def test_an_explicit_range_is_accepted(self) -> None:
        with client_with([ROW]) as client:
            body = client.get(
                "/api/v1/metrics/latency"
                "?start=2026-08-12T10:00:00%2B00:00&end=2026-08-12T11:00:00%2B00:00"
            ).json()

        assert body["window"]["start"].startswith("2026-08-12T10:00")

    def test_half_a_range_is_rejected_rather_than_guessed(self) -> None:
        with client_with([ROW]) as client:
            response = client.get("/api/v1/metrics/latency?start=2026-08-12T10:00:00%2B00:00")
        assert response.status_code == 422
        assert "together" in response.json()["detail"]

    def test_a_malformed_timestamp_is_rejected(self) -> None:
        with client_with([ROW]) as client:
            response = client.get("/api/v1/metrics/latency?start=yesterday&end=today")
        assert response.status_code == 422

    def test_an_invalid_severity_is_rejected(self) -> None:
        with client_with([ROW]) as client:
            assert client.get("/api/v1/anomalies?severity=catastrophic").status_code == 422

    def test_anomaly_pagination_is_echoed_back(self) -> None:
        with client_with([ROW]) as client:
            body = client.get("/api/v1/anomalies?limit=10&offset=20").json()
        assert (body["limit"], body["offset"]) == (10, 20)

    def test_a_clickhouse_outage_is_a_503_not_a_500(self) -> None:
        """The analytics store is upstream of the gateway; say so honestly."""
        from gateway.main import app

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        app.state.queries = MetricsQueries(
            url="http://clickhouse:8123",
            database="db",
            user="u",
            password="p",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        app.state.cache = MetricsCache(None)

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/metrics/latency")

        assert response.status_code == 503
        assert "analytics store" in response.json()["detail"]


class TestProxyRoutes:
    def _upstream(self, status_code: int = 200, payload: dict | None = None):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status_code, json=payload or {"ok": True, "url": str(request.url)}
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def test_a_trace_request_is_forwarded_to_the_debug_service(self) -> None:
        with client_with([], self._upstream()) as client:
            body = client.get("/api/v1/traces/abc123").json()
        assert "debug-service:8003/api/v1/traces/abc123" in body["url"]

    def test_debug_query_is_forwarded(self) -> None:
        with client_with([], self._upstream()) as client:
            body = client.get("/api/v1/debug/query/q-1").json()
        assert "/api/v1/debug/query/q-1" in body["url"]

    def test_ingest_is_forwarded_to_the_collector(self) -> None:
        with client_with([], self._upstream(202, {"accepted": 1})) as client:
            response = client.post("/api/v1/telemetry/event", json={"query_id": "q"})

        assert response.status_code == 202
        assert response.json()["accepted"] == 1

    def test_an_upstream_error_status_is_passed_through(self) -> None:
        """A 404 from the debug service must not become a 200 or a 500."""
        with client_with([], self._upstream(404, {"detail": "no such trace"})) as client:
            response = client.get("/api/v1/traces/missing")

        assert response.status_code == 404
        assert response.json()["detail"] == "no such trace"

    def test_an_unreachable_upstream_is_a_502(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with client_with([], upstream) as client:
            response = client.get("/api/v1/traces/abc")

        assert response.status_code == 502
        assert "upstream service unavailable" in response.json()["detail"]


#: The routes the README's API reference table promises. The served schema must
#: match this exactly, or the documentation is lying to an adopter.
DOCUMENTED_ROUTES: list[tuple[str, str]] = [
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
]


class TestApiContract:
    """The README documents these routes; the schema must match it."""

    @pytest.mark.parametrize(("method", "path"), DOCUMENTED_ROUTES)
    def test_documented_route_exists(self, method: str, path: str) -> None:
        with client_with([]) as client:
            schema = client.get("/openapi.json").json()
        assert path in schema["paths"], f"{path} is documented but not served"
        assert method in schema["paths"][path]

    def test_health_reports_its_dependencies(self) -> None:
        with client_with([{"1": 1}]) as client:
            body = client.get("/health").json()

        assert body["service"] == "api-gateway"
        assert body["clickhouse"] is True
        assert body["cache"] is False


@pytest.mark.asyncio
async def test_counts_are_returned_as_numbers_not_strings() -> None:
    """ClickHouse quotes 64-bit integers by default, which would push the
    parsing problem onto every consumer of the API."""
    capture: dict = {}
    await queries_returning([], capture).summary(WINDOW)

    assert capture["params"]["output_format_json_quote_64bit_integers"] == "0"


class TestAliasShadowing:
    """ClickHouse resolves SELECT aliases inside WHERE.

    `toString(timestamp) AS timestamp` therefore makes an unqualified
    `WHERE timestamp BETWEEN …` compare a String to a DateTime, which fails with
    NO_COMMON_TYPE at query time — a 503 that only appears against a real
    ClickHouse.
    """

    @pytest.mark.asyncio
    async def test_slowest_queries_filters_a_qualified_column(self) -> None:
        capture: dict = {}
        await queries_returning([], capture).slowest_queries(WINDOW)

        assert "events.timestamp BETWEEN" in capture["sql"]

    @pytest.mark.asyncio
    async def test_anomalies_filters_a_qualified_column(self) -> None:
        capture: dict = {}
        await queries_returning([], capture).anomalies(WINDOW)

        assert "anomalies.window_start BETWEEN" in capture["sql"]

    def test_no_query_filters_on_a_column_its_own_alias_shadows(self) -> None:
        """A guard for queries added later, checked per method rather than per file.

        Alias scope is per query, so each method is inspected on its own.
        """
        import inspect
        import re

        from gateway.queries import MetricsQueries

        for name, method in inspect.getmembers(MetricsQueries, inspect.isfunction):
            source = inspect.getsource(method)
            shadowed = set(re.findall(r"toString\((\w+)\)\s+AS\s+\1", source))
            for column in shadowed:
                assert not re.search(
                    rf"WHERE {column}\b", source
                ), f"{name}: WHERE {column} resolves to its own String alias"


class TestMetricCardinality:
    """A label whose values are unbounded eventually kills Prometheus.

    The proxy routes carry trace and query ids in the path, so labelling by
    path would create one series per identifier — found by looking at a Grafana
    legend that had a line per trace.
    """

    def test_proxy_metrics_are_labelled_by_route_not_by_path(self) -> None:
        import inspect

        import gateway.main as main

        source = inspect.getsource(main)
        assert (
            "REQUESTS.labels(endpoint=path" not in source
        ), "labelling by concrete path gives every identifier its own series"
        assert "REQUESTS.labels(endpoint=endpoint" in source

    def test_every_proxy_route_passes_a_stable_endpoint_name(self) -> None:
        import inspect

        import gateway.main as main

        source = inspect.getsource(main)
        for name in ("traces", "debug_query", "replay", "ingest_event", "ingest_batch"):
            assert f'"{name}"' in source, f"proxy route {name} has no stable metric label"

    def test_the_exported_metrics_have_bounded_label_values(self) -> None:
        """Drive several distinct ids and confirm they collapse to one series."""
        from prometheus_client import generate_latest

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with client_with([], upstream) as client:
            for identifier in ("trace-a", "trace-b", "trace-c"):
                client.get(f"/api/v1/traces/{identifier}")

        exported = generate_latest().decode()
        series = [line for line in exported.splitlines() if "gateway_requests_total{" in line]
        trace_series = [line for line in series if 'endpoint="traces"' in line]

        assert trace_series, "no series recorded for the traces route"
        assert not any("trace-a" in line for line in series), "a trace id leaked into a label"
