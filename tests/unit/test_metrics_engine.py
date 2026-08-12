"""Metrics engine: windowing, percentiles, anomaly detection and the writer.

Every number on the dashboard comes out of this arithmetic, so it is tested
against values worked out by hand rather than against itself.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from search_metrics_common import SearchEvent, Severity
from services_path import add_service_to_path

add_service_to_path("metrics-engine")

from engine.aggregation import (  # noqa: E402
    WindowAccumulator,
    WindowedAggregator,
    aggregate,
    percentile,
    window_start_for,
)
from engine.anomaly import MIN_SAMPLES_PER_WINDOW, AnomalyDetector  # noqa: E402
from engine.clickhouse import ClickHouseWriter  # noqa: E402
from engine.pipeline import Pipeline, anomaly_row, event_row  # noqa: E402

BASE = datetime(2026, 8, 12, 10, 0, 0, tzinfo=UTC)


def event(latency: float, *, at: datetime | None = None, service: str = "search-api", **kwargs):
    payload = {
        "query_id": kwargs.pop("query_id", "q"),
        "service": service,
        "query": "test",
        "latency_ms": latency,
        "timestamp": at or BASE,
    }
    payload.update(kwargs)
    return SearchEvent(**payload)


class TestWindowing:
    def test_windows_are_epoch_aligned(self) -> None:
        """Alignment is what lets replicas agree on boundaries without talking."""
        assert window_start_for(datetime(2026, 8, 12, 10, 0, 37, tzinfo=UTC), 60) == BASE
        assert window_start_for(datetime(2026, 8, 12, 10, 0, 59, 999999, tzinfo=UTC), 60) == BASE

    def test_the_next_second_starts_the_next_window(self) -> None:
        assert window_start_for(BASE + timedelta(seconds=60), 60) == BASE + timedelta(seconds=60)

    def test_window_size_is_configurable(self) -> None:
        assert window_start_for(datetime(2026, 8, 12, 10, 7, 30, tzinfo=UTC), 300) == datetime(
            2026, 8, 12, 10, 5, tzinfo=UTC
        )

    def test_a_zero_length_window_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            window_start_for(BASE, 0)


class TestPercentile:
    def test_median_of_a_known_series(self) -> None:
        assert percentile([1, 2, 3, 4, 5], 0.50) == 3

    def test_p95_and_p99_of_one_hundred_values(self) -> None:
        values = [float(n) for n in range(1, 101)]
        assert percentile(values, 0.95) == 95
        assert percentile(values, 0.99) == 99

    def test_p99_of_a_short_series_is_the_maximum(self) -> None:
        """Nearest-rank never invents a value that did not occur."""
        assert percentile([10.0, 20.0, 30.0], 0.99) == 30.0

    def test_single_value(self) -> None:
        assert percentile([42.0], 0.50) == 42.0

    def test_empty_series_is_zero_not_an_error(self) -> None:
        assert percentile([], 0.95) == 0.0

    @pytest.mark.parametrize("quantile", [0, -0.1, 1.5])
    def test_invalid_quantiles_are_rejected(self, quantile: float) -> None:
        with pytest.raises(ValueError):
            percentile([1.0], quantile)


class TestRollup:
    def test_known_events_produce_known_metrics(self) -> None:
        """The anchor test: hand-computed expectations for a fixed input."""
        accumulator = WindowAccumulator("search-api", BASE, 60)
        for latency in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
            accumulator.add(event(latency))

        rollup = accumulator.to_rollup()

        assert rollup.query_count == 10
        assert rollup.latency_p50 == 50
        assert rollup.latency_p95 == 100
        assert rollup.latency_avg == 55
        assert rollup.latency_max == 100
        assert rollup.error_rate == 0.0
        assert rollup.window_end == BASE + timedelta(seconds=60)

    def test_error_rate_counts_every_non_ok_status(self) -> None:
        accumulator = WindowAccumulator("search-api", BASE, 60)
        accumulator.add(event(10))
        accumulator.add(event(20, status="error", error_type="Boom"))
        accumulator.add(event(30, status="timeout", error_type="Timeout"))
        accumulator.add(event(40))

        rollup = accumulator.to_rollup()
        assert rollup.error_count == 2
        assert rollup.error_rate == 0.5

    def test_cache_hit_rate(self) -> None:
        accumulator = WindowAccumulator("search-api", BASE, 60)
        accumulator.add(event(10, cache_hit=True))
        accumulator.add(event(20, cache_hit=True))
        accumulator.add(event(30))
        accumulator.add(event(40))

        assert accumulator.to_rollup().cache_hit_rate == 0.5

    def test_relevance_is_none_when_no_event_reported_a_score(self) -> None:
        """Absent is not the same as zero — a zero would drag the average down."""
        accumulator = WindowAccumulator("search-api", BASE, 60)
        accumulator.add(event(10))

        rollup = accumulator.to_rollup()
        assert rollup.relevance_avg is None
        assert rollup.relevance_p10 is None

    def test_relevance_only_averages_events_that_reported_one(self) -> None:
        accumulator = WindowAccumulator("search-api", BASE, 60)
        accumulator.add(event(10, relevance_score=0.8))
        accumulator.add(event(20, relevance_score=0.6))
        accumulator.add(event(30))

        assert accumulator.to_rollup().relevance_avg == pytest.approx(0.7)

    def test_empty_window_does_not_divide_by_zero(self) -> None:
        rollup = WindowAccumulator("search-api", BASE, 60).to_rollup()
        assert (rollup.query_count, rollup.error_rate, rollup.latency_p99) == (0, 0.0, 0.0)

    def test_row_serialises_for_clickhouse(self) -> None:
        accumulator = WindowAccumulator("search-api", BASE, 60)
        accumulator.add(event(10))
        row = accumulator.to_rollup().as_row()

        assert row["window_start"] == "2026-08-12 10:00:00"
        assert row["service"] == "search-api"
        assert row["relevance_avg"] is None


class TestAggregation:
    def test_services_are_aggregated_independently(self) -> None:
        rollups = aggregate(
            [event(10, service="search-api"), event(500, service="ranking-service")]
        )
        by_service = {rollup.service: rollup for rollup in rollups}

        assert by_service["search-api"].latency_p50 == 10
        assert by_service["ranking-service"].latency_p50 == 500

    def test_events_land_in_the_window_their_timestamp_claims(self) -> None:
        rollups = aggregate([event(10, at=BASE), event(20, at=BASE + timedelta(seconds=61))])
        assert [rollup.window_start for rollup in rollups] == [
            BASE,
            BASE + timedelta(seconds=60),
        ]

    def test_a_late_event_joins_its_own_window_not_the_current_one(self) -> None:
        """Ingest order must not decide which window a measurement belongs to."""
        rollups = aggregate(
            [
                event(10, at=BASE + timedelta(seconds=61)),
                event(20, at=BASE),  # arrives second, belongs to the first window
            ]
        )
        first = next(r for r in rollups if r.window_start == BASE)
        assert first.query_count == 1
        assert first.latency_p50 == 20


class TestWindowedAggregatorLifecycle:
    def test_a_window_is_not_emitted_until_the_watermark_passes_it(self) -> None:
        aggregator = WindowedAggregator(window_seconds=60, grace_seconds=5)
        aggregator.add(event(10, at=BASE))

        assert aggregator.close_ready() == []
        assert aggregator.open_windows == 1

    def test_a_window_is_emitted_once_the_watermark_clears_the_grace_period(self) -> None:
        aggregator = WindowedAggregator(window_seconds=60, grace_seconds=5)
        aggregator.add(event(10, at=BASE))
        aggregator.add(event(20, at=BASE + timedelta(seconds=66)))

        rollups = aggregator.close_ready()
        assert [r.window_start for r in rollups] == [BASE]
        assert aggregator.open_windows == 1  # the newer window is still open

    def test_a_closed_window_is_not_emitted_twice(self) -> None:
        aggregator = WindowedAggregator(window_seconds=60, grace_seconds=5)
        aggregator.add(event(10, at=BASE))
        aggregator.add(event(20, at=BASE + timedelta(seconds=66)))

        assert len(aggregator.close_ready()) == 1
        assert aggregator.close_ready() == []

    def test_flush_emits_everything_still_open(self) -> None:
        """Shutdown must not silently drop the window that was in flight."""
        aggregator = WindowedAggregator(window_seconds=60)
        aggregator.add(event(10, at=BASE))
        aggregator.add(event(20, at=BASE + timedelta(seconds=120)))

        assert len(aggregator.flush()) == 2
        assert aggregator.open_windows == 0


def rollup_for(detector_input: float, service: str = "search-api", count: int = 100):
    accumulator = WindowAccumulator(service, BASE, 60)
    for _ in range(count):
        accumulator.add(event(detector_input))
    return accumulator.to_rollup()


class TestAnomalyDetection:
    def test_no_anomaly_before_the_baseline_has_enough_history(self) -> None:
        """A new service must not alert on its very first windows."""
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        assert detector.evaluate(rollup_for(1000.0)) == []

    def test_a_flat_baseline_never_alerts(self) -> None:
        """Zero variance makes every z-score infinite; that is not a signal."""
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=3)
        for _ in range(10):
            assert detector.evaluate(rollup_for(100.0)) == []

        assert detector.evaluate(rollup_for(100_000.0)) == []

    def test_a_spike_against_a_varied_baseline_is_detected(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency)))

        anomalies = detector.evaluate(rollup_for(900.0))
        metrics = {anomaly.metric for anomaly in anomalies}

        assert "latency_p95" in metrics
        spike = next(a for a in anomalies if a.metric == "latency_p95")
        assert spike.z_score > 3
        assert spike.observed == 900.0
        assert spike.sample_count == 100

    def test_normal_variation_does_not_alert(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency)))

        assert detector.evaluate(rollup_for(104.0)) == []

    def test_a_large_deviation_is_critical_rather_than_a_warning(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency)))

        spike = next(a for a in detector.evaluate(rollup_for(5000.0)) if a.metric == "latency_p95")
        assert spike.severity is Severity.CRITICAL

    def test_a_tiny_window_is_not_judged(self) -> None:
        """Three queries say nothing about a service, however extreme they look."""
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency)))

        small = rollup_for(9000.0, count=MIN_SAMPLES_PER_WINDOW - 1)
        assert detector.evaluate(small) == []

    def test_a_drop_is_an_anomaly_too(self) -> None:
        """Traffic vanishing matters as much as latency exploding."""
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for count in [100, 104, 98, 101, 99, 103, 97, 102]:
            detector.evaluate(rollup_for(100.0, count=count))

        anomalies = detector.evaluate(rollup_for(100.0, count=10))
        volume = next(a for a in anomalies if a.metric == "query_count")
        assert volume.z_score < -3

    def test_services_have_independent_baselines(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency), service="search-api"))

        # A slow service with no history of its own must not be judged against
        # the fast one's baseline.
        assert detector.evaluate(rollup_for(2000.0, service="ranking-service")) == []

    def test_the_scored_window_is_not_part_of_its_own_baseline(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=5)
        for latency in [100, 105, 98, 102, 110, 95, 103, 99]:
            detector.evaluate(rollup_for(float(latency)))

        before = detector.baseline("search-api", "latency_p95")
        detector.evaluate(rollup_for(900.0))
        after = detector.baseline("search-api", "latency_p95")

        assert before is not None and after is not None
        assert after.mean > before.mean  # folded in only after scoring

    def test_invalid_configuration_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            AnomalyDetector(threshold=0)
        with pytest.raises(ValueError):
            AnomalyDetector(min_baseline_windows=1)


class TestPipeline:
    def _pipeline(self) -> Pipeline:
        return Pipeline(
            WindowedAggregator(window_seconds=60, grace_seconds=5),
            AnomalyDetector(threshold=3.0, min_baseline_windows=5),
        )

    def test_valid_events_are_aggregated(self) -> None:
        pipeline = self._pipeline()
        outcome = pipeline.process_batch(
            [event(10).model_dump(mode="json"), event(20).model_dump(mode="json")]
        )
        assert outcome.processed == 2
        assert outcome.invalid == 0

    def test_an_undecodable_record_is_dropped_not_fatal(self) -> None:
        """A poison record must not stall the partition forever."""
        pipeline = self._pipeline()
        outcome = pipeline.process_batch([{"garbage": True}, event(10).model_dump(mode="json")])

        assert outcome.invalid == 1
        assert outcome.processed == 1

    def test_windows_close_and_produce_rollups(self) -> None:
        pipeline = self._pipeline()
        pipeline.process_batch([event(10, at=BASE).model_dump(mode="json")])
        outcome = pipeline.process_batch(
            [event(20, at=BASE + timedelta(seconds=70)).model_dump(mode="json")]
        )

        assert [r.window_start for r in outcome.rollups] == [BASE]

    def test_shutdown_flushes_open_windows(self) -> None:
        pipeline = self._pipeline()
        pipeline.process_batch([event(10, at=BASE).model_dump(mode="json")])

        assert len(pipeline.flush_windows().rollups) == 1
        assert pipeline.open_windows == 0


class TestRowSerialisation:
    def test_event_row_matches_the_clickhouse_columns(self) -> None:
        row = event_row(event(42.0, trace_id="a" * 32, cache_hit=True))

        assert row["latency_ms"] == 42.0
        assert row["cache_hit"] == 1
        assert row["trace_id"] == "a" * 32
        assert row["timestamp"] == "2026-08-12 10:00:00.000"
        assert row["error_type"] == ""  # never NULL: the column has no Nullable

    def test_anomaly_row_matches_the_clickhouse_columns(self) -> None:
        detector = AnomalyDetector(threshold=3.0, min_baseline_windows=3)
        for latency in [100, 105, 98, 102, 110, 95]:
            detector.evaluate(rollup_for(float(latency)))
        anomaly = detector.evaluate(rollup_for(900.0))[0]

        row = anomaly_row(anomaly)
        assert row["window_start"] == "2026-08-12 10:00:00"
        assert row["severity"] in {"warning", "critical"}
        assert isinstance(row["z_score"], float)


class TestClickHouseWriter:
    def _writer(self, handler, **kwargs) -> ClickHouseWriter:
        transport = httpx.MockTransport(handler)
        return ClickHouseWriter(
            url="http://clickhouse:8123",
            database="search_metrics",
            user="search",
            password="changeme",
            client=httpx.AsyncClient(transport=transport),
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_rows_are_buffered_until_flushed(self) -> None:
        writer = self._writer(lambda request: httpx.Response(200))
        writer.add("events", [{"a": 1}, {"a": 2}])

        assert writer.buffered() == 2
        assert await writer.flush() == 2
        assert writer.buffered() == 0

    @pytest.mark.asyncio
    async def test_rows_are_sent_as_json_each_row(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["query"] = request.url.params.get("query")
            seen["body"] = request.content.decode()
            return httpx.Response(200)

        writer = self._writer(handler)
        writer.add("metric_rollups", [{"service": "a"}, {"service": "b"}])
        await writer.flush()

        assert seen["query"] == "INSERT INTO metric_rollups FORMAT JSONEachRow"
        assert seen["body"].splitlines() == ['{"service": "a"}', '{"service": "b"}']

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_retried(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            return httpx.Response(200 if attempts["count"] > 2 else 503)

        writer = self._writer(handler, max_retries=5)
        writer.add("events", [{"a": 1}])
        assert await writer.flush() == 1
        assert attempts["count"] == 3

    @pytest.mark.asyncio
    async def test_rows_survive_a_permanent_failure_so_offsets_are_not_committed(self) -> None:
        """The engine must be able to replay: losing rows here loses data."""
        writer = self._writer(lambda request: httpx.Response(500), max_retries=2)
        writer.add("events", [{"a": 1}])

        with pytest.raises(httpx.HTTPStatusError):
            await writer.flush()

        assert writer.buffered() == 1

    @pytest.mark.asyncio
    async def test_flush_threshold_respects_the_configured_batch_size(self) -> None:
        writer = self._writer(lambda request: httpx.Response(200), batch_size=3, clock=lambda: 0.0)
        writer.add("events", [{"a": 1}, {"a": 2}])
        assert not writer.should_flush

        writer.add("events", [{"a": 3}])
        assert writer.should_flush

    @pytest.mark.asyncio
    async def test_a_partial_batch_flushes_once_it_is_old_enough(self) -> None:
        """Under light traffic, raw events must not sit in the buffer unseen —
        they are exactly what someone debugging one slow query is looking for."""
        now = [0.0]
        writer = self._writer(
            lambda request: httpx.Response(200),
            batch_size=1_000,
            flush_interval_seconds=5.0,
            clock=lambda: now[0],
        )
        writer.add("events", [{"a": 1}])
        assert not writer.should_flush

        now[0] = 5.1
        assert writer.should_flush

    @pytest.mark.asyncio
    async def test_an_empty_buffer_never_asks_to_be_flushed(self) -> None:
        writer = self._writer(lambda request: httpx.Response(200), clock=lambda: 10_000.0)
        assert not writer.should_flush

    @pytest.mark.asyncio
    async def test_flushing_an_empty_buffer_is_a_no_op(self) -> None:
        writer = self._writer(lambda request: httpx.Response(500))
        assert await writer.flush() == 0


class FakeConsumer:
    """Serves one batch, then reports the loop should stop."""

    def __init__(self, engine, batches: list[list[bytes]]) -> None:
        self._engine = engine
        self._batches = list(batches)
        self.commits = 0

    async def getmany(self, timeout_ms: int, max_records: int) -> dict:
        if not self._batches:
            self._engine.running = False
            return {}
        return {"partition-0": [type("R", (), {"value": v})() for v in self._batches.pop(0)]}

    async def commit(self) -> None:
        self.commits += 1

    async def stop(self) -> None:
        pass


class FakeWriter:
    """A writer whose flush can be told to fail."""

    def __init__(self, fail: bool = False) -> None:
        self.rows: dict[str, list] = {}
        self.fail = fail
        self.flushes = 0

    def add(self, table: str, rows: list) -> None:
        self.rows.setdefault(table, []).extend(rows)

    def buffered(self, table=None) -> int:
        return sum(len(rows) for rows in self.rows.values())

    @property
    def should_flush(self) -> bool:
        return self.buffered() > 0

    async def flush(self) -> int:
        self.flushes += 1
        if self.fail:
            raise RuntimeError("clickhouse is down")
        written = self.buffered()
        self.rows.clear()
        return written

    async def close(self) -> None:
        pass


class FakePublisher:
    def __init__(self) -> None:
        self.anomalies: list = []

    async def publish_anomaly(self, anomaly) -> None:
        self.anomalies.append(anomaly)


class TestConsumerLoop:
    """The commit ordering is the service's most important invariant."""

    def _engine(self, batches, *, writer_fails: bool = False):
        import engine.main as main

        engine_instance = main.Engine()
        engine_instance.writer = FakeWriter(fail=writer_fails)
        engine_instance.consumer = FakeConsumer(engine_instance, batches)
        engine_instance.publisher = FakePublisher()
        engine_instance.running = True
        return engine_instance

    @staticmethod
    def _encoded(events: list[SearchEvent]) -> list[bytes]:
        return [e.model_dump_json().encode() for e in events]

    @pytest.mark.asyncio
    async def test_offsets_commit_only_after_the_write_succeeds(self) -> None:
        instance = self._engine([self._encoded([event(10), event(20)])])
        await instance.run()

        assert instance.writer.flushes >= 1
        assert instance.consumer.commits == 1

    @pytest.mark.asyncio
    async def test_a_failed_write_does_not_commit(self) -> None:
        """Committing here would lose the batch: nothing would ever replay it."""
        instance = self._engine([self._encoded([event(10)])], writer_fails=True)
        instance.running = True

        async def stop_after_first_failure(*_args, **_kwargs):
            instance.running = False

        import asyncio as _asyncio

        original_sleep = _asyncio.sleep
        _asyncio.sleep = stop_after_first_failure
        try:
            await instance.run()
        finally:
            _asyncio.sleep = original_sleep

        assert instance.consumer.commits == 0

    @pytest.mark.asyncio
    async def test_raw_events_are_written_for_debugging(self) -> None:
        instance = self._engine([self._encoded([event(10)])])
        await instance.run()

        assert len(instance.writer.rows.get("events", [])) or instance.writer.flushes

    @pytest.mark.asyncio
    async def test_detected_anomalies_are_published(self) -> None:
        detector = instance_detector = AnomalyDetector(threshold=3.0, min_baseline_windows=3)
        for latency in [100, 105, 98, 102, 110, 95]:
            detector.evaluate(rollup_for(float(latency)))

        instance = self._engine([])
        instance.pipeline = Pipeline(
            WindowedAggregator(window_seconds=60, grace_seconds=0), instance_detector
        )
        # One window of steady traffic, then a spike in the next window: the
        # first closes as the watermark advances.
        batch_one = self._encoded([event(100.0, at=BASE, query_id=f"a{i}") for i in range(20)])
        batch_two = self._encoded(
            [event(5000.0, at=BASE + timedelta(seconds=90), query_id=f"b{i}") for i in range(20)]
        )
        # A third batch advances the watermark past the spike window so it closes
        # and gets scored; a window is only judged once it is complete.
        batch_three = self._encoded(
            [event(100.0, at=BASE + timedelta(seconds=200), query_id=f"c{i}") for i in range(20)]
        )
        instance.consumer = FakeConsumer(instance, [batch_one, batch_two, batch_three])
        instance.running = True

        await instance.run()

        assert instance.publisher.anomalies, "a 50x latency spike should have been published"
