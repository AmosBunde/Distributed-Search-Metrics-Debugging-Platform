"""Query simulator: scenario schedules, event realism and rate pacing.

Generation is seeded, so these assert on actual generated traffic rather than on
the shape of the code.
"""

import random
import statistics
from datetime import UTC, datetime

import pytest
from search_metrics_common import SearchEvent
from services_path import add_service_to_path

add_service_to_path("query-simulator")

from simulator.generator import (  # noqa: E402
    generate_batch,
    generate_event,
    log_normal_latency,
)
from simulator.runner import MAX_BATCH, RunStats, SimulationRunner, plan_batches  # noqa: E402
from simulator.scenarios import (  # noqa: E402
    SCENARIOS,
    SERVICES,
    Phase,
    get_scenario,
)

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
STEADY = Phase(name="steady", duration_seconds=60)


class TestScenarios:
    @pytest.mark.parametrize("name", sorted(SCENARIOS))
    def test_every_scenario_has_phases_and_a_description(self, name: str) -> None:
        scenario = get_scenario(name)
        assert scenario.phases
        assert scenario.description

    def test_an_unknown_scenario_names_the_alternatives(self) -> None:
        with pytest.raises(ValueError, match="choose from"):
            get_scenario("chaos-monkey")

    def test_the_documented_scenarios_all_exist(self) -> None:
        """The README promises these by name."""
        assert {"baseline", "error_spike", "slow_queries", "anomaly_spike"} <= set(SCENARIOS)

    def test_phase_lookup_follows_the_schedule(self) -> None:
        scenario = get_scenario("error_spike")
        assert scenario.phase_at(0).name == "warmup"
        assert scenario.phase_at(150).name == "failing"
        assert scenario.phase_at(400).name == "recovery"

    def test_time_past_the_end_holds_the_last_phase(self) -> None:
        """A run longer than the scenario should not crash or loop oddly."""
        scenario = get_scenario("error_spike")
        assert scenario.phase_at(999_999).name == "recovery"

    def test_anomaly_scenarios_warm_up_before_they_spike(self) -> None:
        """The detector needs normal history first, or it reports nothing."""
        scenario = get_scenario("anomaly_spike")
        first = scenario.phases[0]

        assert first.latency_multiplier == 1.0
        assert first.duration_seconds >= 300, "too short to build a baseline"
        assert any(phase.latency_multiplier > 5 for phase in scenario.phases)

    def test_a_traffic_drop_scenario_exists(self) -> None:
        """Volume falling off a cliff is an anomaly too."""
        drop = get_scenario("traffic_drop")
        assert any(phase.qps_multiplier < 0.2 for phase in drop.phases)


class TestLatencyDistribution:
    def test_the_median_lands_where_it_was_asked_to(self) -> None:
        rng = random.Random(42)
        samples = [log_normal_latency(rng, 100.0, 0.4) for _ in range(20_000)]

        assert statistics.median(samples) == pytest.approx(100.0, rel=0.05)

    def test_the_tail_is_long(self) -> None:
        """A uniform distribution would make percentile panels meaningless."""
        rng = random.Random(42)
        samples = sorted(log_normal_latency(rng, 100.0, 0.5) for _ in range(20_000))
        p50 = samples[len(samples) // 2]
        p99 = samples[int(len(samples) * 0.99)]

        assert p99 > 2.5 * p50

    def test_latency_is_never_negative(self) -> None:
        rng = random.Random(7)
        assert all(log_normal_latency(rng, 50.0, 1.2) > 0 for _ in range(5_000))


class TestEventGeneration:
    def test_generated_events_pass_the_platform_contract(self) -> None:
        """Traffic the collector would reject is not a useful simulator."""
        rng = random.Random(1)
        for _ in range(500):
            SearchEvent.model_validate(generate_event(rng, STEADY, timestamp=NOW))

    def test_the_same_seed_produces_the_same_traffic(self) -> None:
        first = generate_batch(random.Random(99), STEADY, 20, timestamp=NOW)
        second = generate_batch(random.Random(99), STEADY, 20, timestamp=NOW)

        assert [e["query"] for e in first] == [e["query"] for e in second]
        assert [e["latency_ms"] for e in first] == [e["latency_ms"] for e in second]

    def test_different_seeds_produce_different_traffic(self) -> None:
        first = generate_batch(random.Random(1), STEADY, 50, timestamp=NOW)
        second = generate_batch(random.Random(2), STEADY, 50, timestamp=NOW)
        assert [e["latency_ms"] for e in first] != [e["latency_ms"] for e in second]

    def test_traffic_spreads_across_the_known_services(self) -> None:
        batch = generate_batch(random.Random(5), STEADY, 400, timestamp=NOW)
        assert {event["service"] for event in batch} == set(SERVICES)

    def test_a_failed_query_still_reports_a_latency(self) -> None:
        """Zero-latency errors would quietly drag every percentile down."""
        failing = Phase(name="failing", duration_seconds=60, error_rate=1.0, timeout_rate=0.0)
        batch = generate_batch(random.Random(3), failing, 100, timestamp=NOW)

        assert all(event["status"] == "error" for event in batch)
        assert all(event["latency_ms"] > 0 for event in batch)
        assert all(event["error_type"] for event in batch)

    def test_a_timeout_is_reported_as_slow(self) -> None:
        timing_out = Phase(name="t", duration_seconds=60, error_rate=0.0, timeout_rate=1.0)
        batch = generate_batch(random.Random(3), timing_out, 50, timestamp=NOW)

        assert all(event["status"] == "timeout" for event in batch)
        assert all(event["latency_ms"] >= 3_000 for event in batch)

    def test_failed_queries_carry_no_results(self) -> None:
        failing = Phase(name="failing", duration_seconds=60, error_rate=1.0, timeout_rate=0.0)
        batch = generate_batch(random.Random(4), failing, 50, timestamp=NOW)

        assert all(event["result_count"] == 0 for event in batch)
        assert all("relevance_score" not in event for event in batch)

    def test_a_degraded_phase_is_measurably_slower(self) -> None:
        healthy = generate_batch(random.Random(11), STEADY, 400, timestamp=NOW)
        degraded_phase = Phase(name="slow", duration_seconds=60, latency_multiplier=6.0)
        degraded = generate_batch(random.Random(11), degraded_phase, 400, timestamp=NOW)

        healthy_p50 = statistics.median(e["latency_ms"] for e in healthy)
        degraded_p50 = statistics.median(e["latency_ms"] for e in degraded)
        assert degraded_p50 > 3 * healthy_p50

    def test_a_phase_only_affects_the_services_it_names(self) -> None:
        phase = Phase(
            name="partial",
            duration_seconds=60,
            latency_multiplier=10.0,
            affected_services=("index-service",),
        )
        rng = random.Random(13)
        affected = [
            generate_event(rng, phase, service="index-service", timestamp=NOW)["latency_ms"]
            for _ in range(300)
        ]
        untouched = [
            generate_event(rng, phase, service="suggest-service", timestamp=NOW)["latency_ms"]
            for _ in range(300)
        ]

        assert statistics.median(affected) > statistics.median(untouched)

    def test_cache_hits_are_faster_than_misses(self) -> None:
        rng = random.Random(17)
        events = [
            generate_event(rng, STEADY, service="search-api", timestamp=NOW) for _ in range(600)
        ]
        hits = [e["latency_ms"] for e in events if e.get("cache_hit")]
        misses = [e["latency_ms"] for e in events if not e.get("cache_hit")]

        assert hits and misses
        assert statistics.median(hits) < statistics.median(misses)

    def test_relevance_shift_lowers_scores(self) -> None:
        rng = random.Random(19)
        normal = [
            generate_event(rng, STEADY, timestamp=NOW).get("relevance_score") for _ in range(400)
        ]
        shifted_phase = Phase(name="bad", duration_seconds=60, relevance_shift=-0.3)
        shifted = [
            generate_event(rng, shifted_phase, timestamp=NOW).get("relevance_score")
            for _ in range(400)
        ]

        normal_scores = [s for s in normal if s is not None]
        shifted_scores = [s for s in shifted if s is not None]
        assert statistics.mean(shifted_scores) < statistics.mean(normal_scores)

    def test_query_ids_are_unique(self) -> None:
        batch = generate_batch(random.Random(23), STEADY, 1_000, timestamp=NOW)
        assert len({event["query_id"] for event in batch}) == 1_000


class TestPacing:
    @pytest.mark.parametrize(
        ("qps", "expected_size"),
        [(10, 10), (100, 100), (500, 500), (1_000, 500), (5_000, 500)],
    )
    def test_batches_stay_within_the_collectors_limit(self, qps, expected_size) -> None:
        size, _ = plan_batches(qps)
        assert size == expected_size
        assert size <= MAX_BATCH

    def test_the_plan_delivers_the_requested_rate(self) -> None:
        for qps in (10, 250, 500, 2_000, 5_000):
            size, interval = plan_batches(qps)
            assert size / interval == pytest.approx(qps, rel=0.02)

    def test_a_non_positive_rate_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_batches(0)


class FakeResponse:
    def __init__(self, status_code: int = 202, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {"accepted": 0, "rejected": 0}
        self.text = str(self._payload)

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, response=None, fail: bool = False) -> None:
        self.response = response or FakeResponse()
        self.fail = fail
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict) -> FakeResponse:
        if self.fail:
            raise ConnectionError("collector unreachable")
        self.calls.append(json)
        return self.response


class TestRunner:
    def _runner(self, client, qps: float = 100.0, duration: int = 1) -> SimulationRunner:
        return SimulationRunner(
            client=client,
            endpoint="http://collector/api/v1/telemetry/batch",
            scenario=get_scenario("baseline"),
            qps=qps,
            duration_seconds=duration,
            seed=1,
        )

    @pytest.mark.asyncio
    async def test_a_run_sends_batches_and_counts_them(self) -> None:
        client = FakeClient(FakeResponse(202, {"accepted": 10, "rejected": 0}))
        stats = await self._runner(client, qps=100, duration=1).run()

        assert stats.batches > 0
        assert stats.sent > 0
        assert stats.accepted > 0
        assert client.calls[0]["events"]

    @pytest.mark.asyncio
    async def test_rejections_are_counted_separately(self) -> None:
        client = FakeClient(FakeResponse(202, {"accepted": 8, "rejected": 2}))
        stats = await self._runner(client, qps=50, duration=1).run()

        assert stats.rejected > 0

    @pytest.mark.asyncio
    async def test_an_unreachable_collector_is_counted_not_fatal(self) -> None:
        stats = await self._runner(FakeClient(fail=True), qps=50, duration=1).run()

        assert stats.failed_requests > 0
        assert stats.sent == 0

    @pytest.mark.asyncio
    async def test_an_error_status_is_counted_as_a_failure(self) -> None:
        client = FakeClient(FakeResponse(413, {"detail": "too large"}))
        stats = await self._runner(client, qps=50, duration=1).run()

        assert stats.failed_requests > 0

    @pytest.mark.asyncio
    async def test_the_achieved_rate_is_close_to_the_target(self) -> None:
        """A simulator that quietly delivers half the requested rate is a liar."""
        client = FakeClient(FakeResponse(202, {"accepted": 50, "rejected": 0}))
        stats = await self._runner(client, qps=200, duration=2).run()

        assert stats.achieved_qps == pytest.approx(200, rel=0.35)

    def test_the_summary_reports_drift_against_the_target(self) -> None:
        stats = RunStats(sent=100, accepted=100, batches=10)
        assert "qps" in stats.summary(100)
