"""Z-score anomaly detection over a rolling per-service baseline.

A window is anomalous relative to *its own recent history*, not to a fixed
threshold. A service that normally answers in 800 ms is not broken because it
crossed 500 ms, and a service that normally answers in 5 ms very much is.

Two cases must never produce an alert, and both are enforced here rather than
downstream:

* **Cold start** — too few baseline windows, or a baseline with zero variance.
  A brand new service, or one under perfectly steady synthetic load, would
  otherwise report an infinite z-score on its first wobble.
* **Too small a sample** — a window with three queries in it says nothing about
  a service's latency, however extreme the number looks.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass

from search_metrics_common import AnomalyEvent, Severity

from .aggregation import MetricRollup

#: Metrics the detector watches, and how to read one out of a rollup.
TRACKED_METRICS: dict[str, str] = {
    "latency_p95": "latency_p95",
    "latency_p99": "latency_p99",
    "error_rate": "error_rate",
    "query_count": "query_count",
}

#: Below this, a window is treated as too small to judge.
MIN_SAMPLES_PER_WINDOW = 10
#: A z-score this far out is reported as critical rather than a warning.
CRITICAL_ZSCORE_MULTIPLIER = 2.0


@dataclass(frozen=True)
class BaselineStats:
    mean: float
    stddev: float
    window_count: int

    @property
    def is_usable(self) -> bool:
        """Zero variance makes every z-score infinite; that is not a signal."""
        return self.stddev > 0


class AnomalyDetector:
    """Keeps a rolling baseline per (service, metric) and scores closed windows."""

    def __init__(
        self,
        threshold: float = 3.0,
        baseline_windows: int = 30,
        min_baseline_windows: int = 5,
    ) -> None:
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if min_baseline_windows < 2:
            raise ValueError("a baseline needs at least two windows")

        self.threshold = threshold
        self.min_baseline_windows = min_baseline_windows
        self._history: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=baseline_windows)
        )

    def baseline(self, service: str, metric: str) -> BaselineStats | None:
        history = self._history[(service, metric)]
        if len(history) < self.min_baseline_windows:
            return None
        values = list(history)
        return BaselineStats(
            mean=statistics.fmean(values),
            stddev=statistics.pstdev(values),
            window_count=len(values),
        )

    def _severity(self, z_score: float) -> Severity:
        if abs(z_score) >= self.threshold * CRITICAL_ZSCORE_MULTIPLIER:
            return Severity.CRITICAL
        return Severity.WARNING

    def evaluate(self, rollup: MetricRollup) -> list[AnomalyEvent]:
        """Score a closed window, then fold it into the baseline.

        Scoring happens *before* the window joins the baseline, so an anomalous
        window is compared against normal history rather than partly against
        itself.
        """
        anomalies: list[AnomalyEvent] = []

        for metric, attribute in TRACKED_METRICS.items():
            observed = float(getattr(rollup, attribute))
            stats = self.baseline(rollup.service, metric)

            if (
                stats is not None
                and stats.is_usable
                and rollup.query_count >= MIN_SAMPLES_PER_WINDOW
            ):
                z_score = (observed - stats.mean) / stats.stddev
                if abs(z_score) >= self.threshold:
                    anomalies.append(
                        AnomalyEvent(
                            service=rollup.service,
                            metric=metric,
                            window_start=rollup.window_start,
                            window_end=rollup.window_end,
                            observed=observed,
                            baseline_mean=stats.mean,
                            baseline_stddev=stats.stddev,
                            z_score=z_score,
                            severity=self._severity(z_score),
                            sample_count=rollup.query_count,
                        )
                    )

            self._history[(rollup.service, metric)].append(observed)

        return anomalies

    def evaluate_all(self, rollups: list[MetricRollup]) -> list[AnomalyEvent]:
        return [anomaly for rollup in rollups for anomaly in self.evaluate(rollup)]

    @property
    def tracked_series(self) -> int:
        return len(self._history)
