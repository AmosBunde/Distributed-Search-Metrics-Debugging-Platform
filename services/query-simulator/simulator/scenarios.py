"""Traffic scenarios: what the platform is being asked to observe.

Each scenario is a schedule of *phases*. A phase says how the world behaves for
a stretch of time — how fast queries arrive, how slow they are, how often they
fail. That structure matters more than the numbers: an anomaly detector can only
be exercised by traffic that is normal first and abnormal afterwards, so every
interesting scenario has a warm-up.

Everything here is deterministic given a seed, so a test can assert on generated
traffic and an investigation can be reproduced exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Services the simulator generates traffic for. These match the rows seeded
#: into the `services` table, so SLO comparisons have something to compare to.
SERVICES: tuple[str, ...] = (
    "search-api",
    "ranking-service",
    "index-service",
    "suggest-service",
)

#: Baseline latency profile per service: (median ms, spread). Log-normal, so
#: the tail is long — which is the point, since p99 is what people watch.
SERVICE_LATENCY: dict[str, tuple[float, float]] = {
    "search-api": (120.0, 0.45),
    "ranking-service": (85.0, 0.5),
    "index-service": (200.0, 0.6),
    "suggest-service": (25.0, 0.35),
}


@dataclass(frozen=True)
class Phase:
    """How traffic behaves for one stretch of a scenario."""

    name: str
    duration_seconds: int
    qps_multiplier: float = 1.0
    latency_multiplier: float = 1.0
    error_rate: float = 0.005
    timeout_rate: float = 0.001
    cache_hit_rate: float = 0.35
    relevance_shift: float = 0.0
    affected_services: tuple[str, ...] = SERVICES

    def applies_to(self, service: str) -> bool:
        return service in self.affected_services


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    phases: tuple[Phase, ...] = field(default_factory=tuple)

    @property
    def total_seconds(self) -> int:
        return sum(phase.duration_seconds for phase in self.phases)

    def phase_at(self, elapsed_seconds: float) -> Phase:
        """Which phase is in effect. The last phase repeats once time runs out."""
        remaining = elapsed_seconds
        for phase in self.phases:
            if remaining < phase.duration_seconds:
                return phase
            remaining -= phase.duration_seconds
        return self.phases[-1]


BASELINE = Scenario(
    name="baseline",
    description="Steady, healthy traffic. What normal looks like.",
    phases=(Phase(name="steady", duration_seconds=3600),),
)

ERROR_SPIKE = Scenario(
    name="error_spike",
    description="A dependency starts failing: errors jump, then recover.",
    phases=(
        Phase(name="warmup", duration_seconds=120),
        Phase(
            name="failing",
            duration_seconds=180,
            error_rate=0.35,
            timeout_rate=0.08,
            latency_multiplier=1.4,
            affected_services=("index-service", "search-api"),
        ),
        Phase(name="recovery", duration_seconds=180, error_rate=0.02),
    ),
)

SLOW_QUERIES = Scenario(
    name="slow_queries",
    description="Latency degrades badly without anything actually failing.",
    phases=(
        Phase(name="warmup", duration_seconds=120),
        Phase(
            name="degraded",
            duration_seconds=300,
            latency_multiplier=6.0,
            cache_hit_rate=0.05,
            affected_services=("ranking-service", "index-service"),
        ),
        Phase(name="recovery", duration_seconds=120),
    ),
)

ANOMALY_SPIKE = Scenario(
    name="anomaly_spike",
    description=(
        "A long calm baseline, then a sharp spike. Built to exercise the "
        "anomaly detector, which needs normal history before it will report "
        "anything."
    ),
    phases=(
        # Long enough to fill the detector's baseline window at 60s windows.
        Phase(name="baseline", duration_seconds=420),
        Phase(
            name="spike",
            duration_seconds=120,
            qps_multiplier=2.5,
            latency_multiplier=12.0,
            error_rate=0.15,
            relevance_shift=-0.25,
        ),
        Phase(name="aftermath", duration_seconds=180),
    ),
)

TRAFFIC_DROP = Scenario(
    name="traffic_drop",
    description="Traffic falls off a cliff — an anomaly that is not a spike.",
    phases=(
        Phase(name="baseline", duration_seconds=420),
        Phase(name="drop", duration_seconds=180, qps_multiplier=0.05),
        Phase(name="recovery", duration_seconds=120),
    ),
)

SCENARIOS: dict[str, Scenario] = {
    scenario.name: scenario
    for scenario in (BASELINE, ERROR_SPIKE, SLOW_QUERIES, ANOMALY_SPIKE, TRAFFIC_DROP)
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise ValueError(
            f"unknown scenario {name!r}; choose from {', '.join(sorted(SCENARIOS))}"
        ) from None
