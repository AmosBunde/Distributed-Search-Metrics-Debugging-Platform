"""Debug service: trace assembly, root cause heuristics, replay diffing, API.

Trace assembly is the risky part — spans arrive out of order, incomplete, and
occasionally corrupt — so it gets the hostile inputs.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from services_path import add_service_to_path

add_service_to_path("debug-service")

from debug_service.replay import (  # noqa: E402
    QueryRun,
    ReplayJob,
    ReplayStatus,
    diff_runs,
    execute_replay,
)
from debug_service.root_cause import FindingKind, analyse, slowest_service, summarise  # noqa: E402
from debug_service.storage import ReplayJobStore  # noqa: E402
from debug_service.trace import CyclicTraceError, Span, build_trace  # noqa: E402

BASE = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def span(
    span_id: str,
    parent: str = "",
    *,
    service: str = "search-api",
    operation: str = "search",
    offset_ms: float = 0,
    duration_ms: float = 100,
    status: str = "ok",
    **attributes,
) -> Span:
    return Span(
        trace_id="trace-1",
        span_id=span_id,
        parent_span_id=parent,
        query_id="q-1",
        service=service,
        operation=operation,
        start_time=BASE + timedelta(milliseconds=offset_ms),
        duration_ms=duration_ms,
        status=status,
        attributes={k: str(v) for k, v in attributes.items()},
    )


class TestTraceAssembly:
    def test_a_simple_chain_nests(self) -> None:
        tree = build_trace([span("a", duration_ms=100), span("b", "a", duration_ms=60)])

        assert len(tree.roots) == 1
        assert tree.roots[0].children[0].span.span_id == "b"
        assert tree.roots[0].depth == 0
        assert tree.roots[0].children[0].depth == 1

    def test_spans_arriving_out_of_order_still_nest(self) -> None:
        """Storage returns whatever order it likes; the tree must not care."""
        tree = build_trace(
            [
                span("c", "b", offset_ms=20, duration_ms=30),
                span("a", duration_ms=100),
                span("b", "a", offset_ms=10, duration_ms=60),
            ]
        )

        root = tree.roots[0]
        assert root.span.span_id == "a"
        assert root.children[0].span.span_id == "b"
        assert root.children[0].children[0].span.span_id == "c"

    def test_children_are_ordered_by_start_time(self) -> None:
        tree = build_trace(
            [
                span("root", duration_ms=100),
                span("late", "root", offset_ms=50, duration_ms=10),
                span("early", "root", offset_ms=5, duration_ms=10),
            ]
        )
        assert [c.span.span_id for c in tree.roots[0].children] == ["early", "late"]

    def test_an_orphan_is_kept_and_flagged_not_dropped(self) -> None:
        """The missing parent is usually the interesting part of the mystery."""
        tree = build_trace([span("a", duration_ms=100), span("b", "missing", duration_ms=50)])

        assert tree.orphan_count == 1
        assert len(tree.roots) == 2
        assert any(node.orphaned for node in tree.nodes)

    def test_a_cycle_is_rejected_rather_than_hanging(self) -> None:
        with pytest.raises(CyclicTraceError):
            build_trace([span("a", "b"), span("b", "a")])

    def test_a_self_referencing_span_is_rejected(self) -> None:
        with pytest.raises(CyclicTraceError):
            build_trace([span("a", "a")])

    def test_no_spans_produces_an_empty_tree(self) -> None:
        tree = build_trace([])
        assert (tree.span_count, tree.roots) == (0, [])

    def test_self_time_excludes_time_spent_in_children(self) -> None:
        """A slow parent whose child did the waiting is not the culprit."""
        tree = build_trace(
            [span("a", duration_ms=1000), span("b", "a", offset_ms=10, duration_ms=950)]
        )

        root = tree.roots[0]
        assert root.self_time_ms == 50
        assert root.children[0].self_time_ms == 950

    def test_services_are_listed(self) -> None:
        tree = build_trace(
            [span("a", service="search-api"), span("b", "a", service="ranking-service")]
        )
        assert tree.services == ["ranking-service", "search-api"]

    def test_critical_path_follows_the_longest_child(self) -> None:
        tree = build_trace(
            [
                span("root", duration_ms=1000),
                span("fast", "root", offset_ms=1, duration_ms=10),
                span("slow", "root", offset_ms=2, duration_ms=900),
                span("slower-child", "slow", offset_ms=3, duration_ms=880),
            ]
        )
        assert [n.span.span_id for n in tree.critical_path()] == ["root", "slow", "slower-child"]

    def test_serialisation_is_nested_and_json_ready(self) -> None:
        payload = build_trace([span("a"), span("b", "a")]).as_dict()

        assert payload["span_count"] == 2
        assert payload["roots"][0]["children"][0]["span_id"] == "b"


class TestRootCause:
    def test_an_error_span_is_the_top_finding(self) -> None:
        tree = build_trace(
            [
                span("a", duration_ms=500),
                span("b", "a", service="index-service", duration_ms=400, status="error"),
            ]
        )
        findings = analyse(tree)

        assert findings[0].kind is FindingKind.ERROR_SPAN
        assert findings[0].service == "index-service"
        assert findings[0].confidence >= 0.9

    def test_a_dominant_span_is_reported_as_slow(self) -> None:
        tree = build_trace(
            [
                span("a", duration_ms=1000),
                span("b", "a", service="ranking-service", offset_ms=10, duration_ms=900),
            ]
        )
        kinds = {finding.kind for finding in analyse(tree)}
        assert FindingKind.SLOW_SPAN in kinds

    def test_a_span_within_its_service_baseline_is_not_a_breach(self) -> None:
        """900ms is unremarkable for a service whose p95 is 850ms."""
        tree = build_trace([span("a", service="ranking-service", duration_ms=900)])
        findings = analyse(tree, baselines={"ranking-service": 850.0})

        assert not any(f.kind is FindingKind.BASELINE_BREACH for f in findings)

    def test_a_span_far_past_its_baseline_is_a_breach(self) -> None:
        tree = build_trace([span("a", service="suggest-service", duration_ms=900)])
        findings = analyse(tree, baselines={"suggest-service": 50.0})

        breach = next(f for f in findings if f.kind is FindingKind.BASELINE_BREACH)
        assert breach.evidence["ratio"] == 18.0
        assert "18.0x" in breach.summary

    def test_repeated_calls_under_one_parent_look_like_retries(self) -> None:
        spans = [span("root", duration_ms=500)]
        spans += [
            span(f"retry-{i}", "root", service="index-service", operation="fetch",
                 offset_ms=10 * i, duration_ms=40)
            for i in range(4)
        ]  # fmt: skip
        findings = analyse(build_trace(spans))

        storm = next(f for f in findings if f.kind is FindingKind.RETRY_STORM)
        assert storm.evidence["call_count"] == 4

    def test_a_cache_miss_is_reported_with_low_confidence(self) -> None:
        """Usually a contributing factor, rarely the cause — the score says so."""
        tree = build_trace([span("a", duration_ms=300, **{"cache.hit": "false"})])
        miss = next(f for f in analyse(tree) if f.kind is FindingKind.CACHE_MISS)

        assert miss.confidence <= 0.6

    def test_missing_spans_are_surfaced_as_a_finding(self) -> None:
        tree = build_trace([span("a"), span("b", "vanished")])
        kinds = {f.kind for f in analyse(tree)}
        assert FindingKind.MISSING_SPANS in kinds

    def test_wide_fan_out_is_reported(self) -> None:
        spans = [span("root", duration_ms=500)]
        spans += [
            span(f"shard-{i}", "root", operation=f"shard{i}", offset_ms=i, duration_ms=20)
            for i in range(9)
        ]
        kinds = {f.kind for f in analyse(build_trace(spans))}
        assert FindingKind.FAN_OUT in kinds

    def test_a_short_span_is_not_called_slow_just_for_dominating_a_short_trace(self) -> None:
        """40 ms of a 100 ms trace is a majority share and still not a problem."""
        tree = build_trace(
            [
                span("a", duration_ms=100),
                span("b", "a", offset_ms=10, duration_ms=30),
                span("c", "a", offset_ms=45, duration_ms=30),
            ]
        )
        assert analyse(tree, baselines={"search-api": 200.0}) == []
        assert "No root cause" in summarise([])

    def test_findings_are_ranked_by_confidence(self) -> None:
        tree = build_trace(
            [
                span("a", duration_ms=1000, **{"cache.hit": "false"}),
                span("b", "a", offset_ms=5, duration_ms=900, status="error"),
            ]
        )
        findings = analyse(tree)

        # Failure first, whatever the analyser's confidence in the latency.
        assert findings[0].kind is FindingKind.ERROR_SPAN
        assert findings[-1].kind is FindingKind.CACHE_MISS

    def test_slowest_service_sums_self_time(self) -> None:
        tree = build_trace(
            [
                span("a", service="search-api", duration_ms=1000),
                span("b", "a", service="ranking-service", offset_ms=10, duration_ms=900),
            ]
        )
        assert slowest_service(tree) == ("ranking-service", 900.0)


class TestReplayDiff:
    def _run(self, **kwargs) -> QueryRun:
        payload = {
            "query": "distributed tracing",
            "latency_ms": 100.0,
            "result_count": 3,
            "status": "ok",
            "document_ids": ["d1", "d2", "d3"],
        }
        payload.update(kwargs)
        return QueryRun(**payload)

    def test_an_identical_replay_matches(self) -> None:
        diff = diff_runs(self._run(), self._run())

        assert diff.results_match
        assert diff.verdict == "matches the original run"
        assert diff.latency_delta_ms == 0

    def test_a_much_slower_replay_is_a_regression(self) -> None:
        diff = diff_runs(self._run(), self._run(latency_ms=400.0))

        assert diff.latency_ratio == 4.0
        assert diff.verdict == "slower than the original run"

    def test_small_latency_variation_is_not_reported_as_slower(self) -> None:
        """Run-to-run noise must not read as a regression."""
        diff = diff_runs(self._run(), self._run(latency_ms=120.0))
        assert diff.verdict == "matches the original run"

    def test_different_documents_are_reported(self) -> None:
        diff = diff_runs(self._run(), self._run(document_ids=["d1", "d2", "d9"]))

        assert not diff.results_match
        assert diff.added_documents == ["d9"]
        assert diff.removed_documents == ["d3"]
        assert diff.common_documents == 2

    def test_reordering_alone_is_not_a_difference(self) -> None:
        """Comparing by set: a ranking shuffle is not a different result set."""
        diff = diff_runs(self._run(), self._run(document_ids=["d3", "d1", "d2"]))
        assert diff.results_match

    def test_a_failure_that_now_succeeds_is_not_reproducible(self) -> None:
        original = self._run(status="error", result_count=0, document_ids=[])
        diff = diff_runs(original, self._run())

        assert diff.status_changed
        assert diff.verdict == "no longer reproducible"

    def test_a_failure_that_still_fails_says_so(self) -> None:
        diff = diff_runs(self._run(), self._run(status="timeout"))
        assert diff.verdict == "still failing"

    def test_a_zero_latency_original_does_not_divide_by_zero(self) -> None:
        diff = diff_runs(self._run(latency_ms=0.0), self._run(latency_ms=50.0))
        assert diff.latency_ratio == 1.0


class TestReplayExecution:
    def _job(self) -> ReplayJob:
        import uuid

        return ReplayJob(id=uuid.uuid4(), query_id="q-1", target_service="search-api")

    @pytest.mark.asyncio
    async def test_a_successful_replay_records_the_diff(self) -> None:
        class Executor:
            async def run(self, query: str, target: str) -> QueryRun:
                return QueryRun(query=query, latency_ms=90.0, result_count=3,
                                document_ids=["d1", "d2", "d3"])  # fmt: skip

        original = QueryRun(
            query="q", latency_ms=100.0, result_count=3, document_ids=["d1", "d2", "d3"]
        )
        job = await execute_replay(self._job(), original, Executor())

        assert job.status is ReplayStatus.SUCCEEDED
        assert job.diff is not None and job.diff.results_match
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_a_broken_target_is_recorded_not_raised(self) -> None:
        """ "The target refused the connection" is an answer to the question."""

        class Executor:
            async def run(self, query: str, target: str) -> QueryRun:
                raise ConnectionError("connection refused")

        original = QueryRun(query="q", latency_ms=100.0, result_count=0)
        job = await execute_replay(self._job(), original, Executor())

        assert job.status is ReplayStatus.FAILED
        assert "connection refused" in job.error
        assert job.completed_at is not None


class TestReplayJobStore:
    @pytest.mark.asyncio
    async def test_jobs_survive_without_a_database(self) -> None:
        """A Postgres outage should degrade the API, not break it."""
        import uuid

        store = ReplayJobStore(pool=None)
        job = ReplayJob(id=uuid.uuid4(), query_id="q-1", target_service="search-api")

        await store.save(job)
        assert (await store.get(str(job.id)))["query_id"] == "q-1"

    @pytest.mark.asyncio
    async def test_an_unknown_job_is_none(self) -> None:
        assert await ReplayJobStore(pool=None).get("missing") is None


class FakeReader:
    def __init__(self, spans=None, event=None, documents=None, baselines=None) -> None:
        self._spans = spans or []
        self._event = event
        self._documents = documents or []
        self._baselines = baselines or {}

    async def spans_for_trace(self, trace_id: str):
        return self._spans

    async def spans_for_query(self, query_id: str):
        return self._spans

    async def event_for_query(self, query_id: str):
        return self._event

    async def documents_for_query(self, query_id: str):
        return self._documents

    async def service_baselines(self, lookback_minutes: int = 60):
        return self._baselines

    async def original_run(self, query_id: str):
        if self._event is None:
            return None
        return QueryRun(
            query=self._event["query"],
            latency_ms=self._event["latency_ms"],
            result_count=self._event["result_count"],
            status=self._event["status"],
            document_ids=self._documents,
        )

    async def close(self) -> None:
        pass


EVENT = {
    "query_id": "q-1",
    "trace_id": "trace-1",
    "service": "search-api",
    "query": "distributed tracing",
    "latency_ms": 1500.0,
    "status": "ok",
    "result_count": 3,
    "relevance_score": 0.8,
    "cache_hit": 0,
    "error_type": "",
    "error_message": "",
    "timestamp": "2026-08-12 10:00:00",
}


@pytest.fixture(autouse=True)
def skip_lifespan(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from debug_service.main import app

    @asynccontextmanager
    async def noop(app):
        yield

    monkeypatch.setattr(app.router, "lifespan_context", noop)


def client_with(reader: FakeReader, executor=None) -> TestClient:
    from debug_service.main import app

    app.state.reader = reader
    app.state.jobs = ReplayJobStore(pool=None)
    app.state.pool = None
    app.state.executor = executor
    return TestClient(app)


class TestApi:
    def test_trace_endpoint_returns_the_assembled_tree(self) -> None:
        spans = [span("a", duration_ms=1000), span("b", "a", offset_ms=10, duration_ms=900)]
        with client_with(FakeReader(spans=spans)) as client:
            body = client.get("/api/v1/traces/trace-1").json()

        assert body["span_count"] == 2
        assert body["roots"][0]["children"][0]["span_id"] == "b"
        assert body["critical_path"][0]["service"] == "search-api"

    def test_an_unknown_trace_is_404(self) -> None:
        with client_with(FakeReader()) as client:
            assert client.get("/api/v1/traces/nope").status_code == 404

    def test_a_corrupt_trace_is_422_rather_than_a_500(self) -> None:
        with client_with(FakeReader(spans=[span("a", "b"), span("b", "a")])) as client:
            assert client.get("/api/v1/traces/trace-1").status_code == 422

    def test_debug_query_returns_ranked_findings(self) -> None:
        spans = [
            span("a", duration_ms=1500),
            span("b", "a", service="index-service", offset_ms=10, duration_ms=1400,
                 status="error"),
        ]  # fmt: skip
        reader = FakeReader(spans=spans, event=EVENT, baselines={"index-service": 100.0})

        with client_with(reader) as client:
            body = client.get("/api/v1/debug/query/q-1").json()

        assert body["findings"][0]["kind"] == "error_span"
        assert "index-service" in body["summary"]
        assert body["slowest_service"]["service"] == "index-service"

    def test_debug_query_for_an_unknown_query_is_404(self) -> None:
        with client_with(FakeReader()) as client:
            assert client.get("/api/v1/debug/query/nope").status_code == 404

    def test_replay_runs_and_returns_a_diff(self) -> None:
        class Executor:
            async def run(self, query: str, target: str) -> QueryRun:
                return QueryRun(query=query, latency_ms=200.0, result_count=3,
                                document_ids=["d1", "d2", "d3"])  # fmt: skip

        reader = FakeReader(event=EVENT, documents=["d1", "d2", "d3"])
        with client_with(reader, Executor()) as client:
            response = client.post("/api/v1/debug/replay", json={"query_id": "q-1"})

        body = response.json()
        assert response.status_code == 202
        assert body["status"] == "succeeded"
        assert body["diff"]["results_match"] is True
        assert body["target_service"] == "search-api"

    def test_replaying_an_unknown_query_is_404(self) -> None:
        with client_with(FakeReader()) as client:
            response = client.post("/api/v1/debug/replay", json={"query_id": "nope"})
        assert response.status_code == 404

    def test_a_replay_job_can_be_fetched_afterwards(self) -> None:
        class Executor:
            async def run(self, query: str, target: str) -> QueryRun:
                return QueryRun(query=query, latency_ms=200.0, result_count=3)

        reader = FakeReader(event=EVENT, documents=["d1"])
        with client_with(reader, Executor()) as client:
            job_id = client.post("/api/v1/debug/replay", json={"query_id": "q-1"}).json()["id"]
            assert client.get(f"/api/v1/debug/replay/{job_id}").json()["query_id"] == "q-1"

    def test_health_reports_dependencies(self) -> None:
        with client_with(FakeReader()) as client:
            body = client.get("/health").json()

        assert body["service"] == "debug-service"
        assert body["postgres"] is False


class TestReplayTargetIsNotAttackerControlled:
    """Replay is the one outbound request whose address a caller influences.

    Without an allowlist it is server-side request forgery: a caller could point
    the service at cloud metadata, an internal admin endpoint, or anything else
    reachable from the pod.
    """

    def test_an_allowed_service_resolves(self) -> None:
        from debug_service.main import resolve_target

        assert resolve_target("search-api", None) == "search-api"

    def test_the_recorded_service_is_used_when_none_is_requested(self) -> None:
        from debug_service.main import resolve_target

        assert resolve_target(None, "ranking-service") == "ranking-service"

    @pytest.mark.parametrize(
        "hostile",
        [
            "169.254.169.254",
            "localhost:8003",
            "search-api/../../admin",
            "evil.example.com",
            "search-api@evil.example.com",
            "http://evil.example.com",
        ],
    )
    def test_a_target_off_the_allowlist_is_refused(self, hostile: str) -> None:
        from debug_service.main import resolve_target

        with pytest.raises(ValueError):
            resolve_target(hostile, None)

    def test_a_recorded_service_is_still_checked(self) -> None:
        """Data from our own store is not automatically trustworthy."""
        from debug_service.main import resolve_target

        with pytest.raises(ValueError):
            resolve_target(None, "169.254.169.254")

    def test_no_target_at_all_is_refused(self) -> None:
        from debug_service.main import resolve_target

        with pytest.raises(ValueError, match="no replay target"):
            resolve_target(None, None)

    def test_the_api_rejects_a_malformed_target_with_422(self) -> None:
        reader = FakeReader(event=EVENT, documents=["d1"])
        with client_with(reader) as client:
            response = client.post(
                "/api/v1/debug/replay",
                json={"query_id": "q-1", "target_service": "http://169.254.169.254"},
            )
        assert response.status_code == 422

    def test_the_api_rejects_a_well_formed_but_disallowed_target_with_400(self) -> None:
        reader = FakeReader(event=EVENT, documents=["d1"])
        with client_with(reader) as client:
            response = client.post(
                "/api/v1/debug/replay",
                json={"query_id": "q-1", "target_service": "internal-admin"},
            )
        assert response.status_code == 400
        assert "not an allowed replay target" in response.json()["detail"]


class TestPostgresPersistence:
    """asyncpg binds by Python type, not by the SQL cast."""

    @pytest.mark.asyncio
    async def test_timestamps_are_bound_as_datetimes_not_strings(self) -> None:
        """A `::timestamptz` cast in the SQL does not make asyncpg accept a string."""
        import uuid
        from datetime import datetime

        captured: dict = {}

        class FakeConnection:
            async def execute(self, sql: str, *args) -> None:
                captured["args"] = args

        class FakePool:
            def acquire(self):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def ctx():
                    yield FakeConnection()

                return ctx()

        store = ReplayJobStore(pool=FakePool())
        job = ReplayJob(id=uuid.uuid4(), query_id="q-1", target_service="search-api")
        await store.save(job)

        requested_at = captured["args"][5]
        assert isinstance(
            requested_at, datetime
        ), f"requested_at must be a datetime, got {type(requested_at).__name__}"

    @pytest.mark.asyncio
    async def test_numeric_columns_are_bound_as_numbers(self) -> None:
        import uuid

        captured: dict = {}

        class FakeConnection:
            async def execute(self, sql: str, *args) -> None:
                captured["args"] = args

        class FakePool:
            def acquire(self):
                from contextlib import asynccontextmanager

                @asynccontextmanager
                async def ctx():
                    yield FakeConnection()

                return ctx()

        job = ReplayJob(id=uuid.uuid4(), query_id="q-1", target_service="search-api")
        job.original = QueryRun(query="q", latency_ms=100.0, result_count=3)

        await ReplayJobStore(pool=FakePool()).save(job)
        assert captured["args"][7] == 100.0
        assert captured["args"][9] == 3
