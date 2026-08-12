"""Driving traffic at a target rate.

The loop is paced by wall clock rather than by sleeping a fixed amount per
batch: if a batch takes longer than its slot, the next one starts immediately
instead of the run silently drifting behind the requested rate. A simulator that
quietly delivers half the QPS you asked for is worse than one that tells you it
could not keep up.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .generator import generate_batch, generate_spans
from .scenarios import Scenario

logger = logging.getLogger(__name__)

#: Batch size is capped by the collector's documented limit.
MAX_BATCH = 500
#: Send at most this often; below it, batches get too small to be efficient.
MAX_BATCHES_PER_SECOND = 10
#: Share of queries that also emit a trace. Production sampling is a real
#: decision (ADR-0004); here it keeps span volume sane while guaranteeing that
#: any run leaves traces to debug.
DEFAULT_TRACE_SAMPLE = 0.05


@dataclass
class RunStats:
    sent: int = 0
    spans_sent: int = 0
    accepted: int = 0
    rejected: int = 0
    failed_requests: int = 0
    batches: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return max(1e-9, time.monotonic() - self.started_at)

    @property
    def achieved_qps(self) -> float:
        return self.sent / self.elapsed_seconds

    def summary(self, target_qps: float) -> str:
        drift = 100 * (self.achieved_qps / target_qps - 1) if target_qps else 0.0
        return (
            f"sent {self.sent} events in {self.elapsed_seconds:.1f}s "
            f"({self.achieved_qps:.0f} qps, {drift:+.0f}% vs target) — "
            f"accepted {self.accepted}, rejected {self.rejected}, "
            f"spans {self.spans_sent}, failed requests {self.failed_requests}"
        )


def plan_batches(qps: float) -> tuple[int, float]:
    """Split a target rate into (batch size, seconds between batches)."""
    if qps <= 0:
        raise ValueError("qps must be positive")

    batches_per_second = min(MAX_BATCHES_PER_SECOND, max(1.0, qps / MAX_BATCH))
    batch_size = max(1, round(qps / batches_per_second))
    return min(batch_size, MAX_BATCH), 1.0 / batches_per_second


class SimulationRunner:
    """Sends generated traffic to the collector for the length of a scenario."""

    def __init__(
        self,
        client: Any,
        endpoint: str,
        scenario: Scenario,
        qps: float,
        duration_seconds: int | None = None,
        seed: int | None = None,
        trace_sample: float = DEFAULT_TRACE_SAMPLE,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.scenario = scenario
        self.qps = qps
        self.duration = duration_seconds or scenario.total_seconds
        self.rng = random.Random(seed)
        self.trace_sample = trace_sample
        self.stats = RunStats()

    async def send(self, events: list[dict[str, Any]]) -> None:
        try:
            response = await self.client.post(self.endpoint, json={"events": events})
        except Exception as exc:
            self.stats.failed_requests += 1
            logger.warning("batch failed to send", extra={"error": str(exc)})
            return

        self.stats.batches += 1
        self.stats.sent += len(events)

        if response.status_code >= 400:
            self.stats.failed_requests += 1
            logger.warning(
                "collector rejected the batch",
                extra={"status": response.status_code, "body": response.text[:200]},
            )
            return

        body = response.json()
        self.stats.accepted += body.get("accepted", 0)
        self.stats.rejected += body.get("rejected", 0)

    async def send_spans(self, spans: list[dict[str, Any]]) -> None:
        try:
            response = await self.client.post(
                self.endpoint.replace("/batch", "/spans"), json={"spans": spans}
            )
        except Exception as exc:
            self.stats.failed_requests += 1
            logger.warning("span batch failed to send", extra={"error": str(exc)})
            return

        if response.status_code >= 400:
            self.stats.failed_requests += 1
            logger.warning("collector rejected the spans", extra={"status": response.status_code})
            return

        self.stats.spans_sent += len(spans)

    async def run(self) -> RunStats:
        batch_size, interval = plan_batches(self.qps)
        start = time.monotonic()
        next_send = start
        current_phase = ""

        logger.info(
            "starting simulation",
            extra={
                "scenario": self.scenario.name,
                "qps": self.qps,
                "duration_seconds": self.duration,
                "batch_size": batch_size,
            },
        )

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= self.duration:
                break

            phase = self.scenario.phase_at(elapsed)
            if phase.name != current_phase:
                current_phase = phase.name
                logger.info(
                    "entering phase",
                    extra={"phase": phase.name, "elapsed_seconds": round(elapsed)},
                )

            size = max(1, round(batch_size * phase.qps_multiplier))
            events = generate_batch(self.rng, phase, size)

            # Spans are generated before the events are sent, because generating
            # them stamps the trace id onto the event that carries it.
            spans = [
                span
                for event in events
                if self.rng.random() < self.trace_sample
                for span in generate_spans(self.rng, event)
            ]

            await self.send(events)
            if spans:
                await self.send_spans(spans)

            # Pace against the schedule, not against how long the send took.
            next_send += interval
            delay = next_send - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_send = time.monotonic()

        logger.info("simulation finished", extra={"summary": self.stats.summary(self.qps)})
        return self.stats
