"""Ranked root cause findings for one trace.

Every finding carries the evidence that produced it and a confidence score.
That is the difference between a debugging tool and a guess: an operator can
disagree with a finding by looking at the same span the analyser looked at.

The rules are deliberately conservative. A slow span is only a root cause if it
is slow *relative to its own service's baseline* — the platform already knows
each service's p95, and a 900 ms span in a service that normally takes 850 ms is
not a finding. Reporting it anyway is how a debugging tool becomes noise that
people stop reading.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .trace import SpanNode, TraceTree


class FindingKind(StrEnum):
    ERROR_SPAN = "error_span"
    SLOW_SPAN = "slow_span"
    BASELINE_BREACH = "baseline_breach"
    RETRY_STORM = "retry_storm"
    CACHE_MISS = "cache_miss"
    MISSING_SPANS = "missing_spans"
    FAN_OUT = "fan_out"


#: A span must own at least this share of the trace to be called slow.
SELF_TIME_SHARE_THRESHOLD = 0.35
#: ...and at least this much absolute time. Without a floor, every trivially
#: short trace produces a "slow span" finding, which is how a tool becomes noise.
MIN_SLOW_SPAN_MS = 100.0
#: Repeats of one operation under a single parent before it looks like retries.
RETRY_THRESHOLD = 3
#: Children of one span before the fan-out itself is the problem.
FAN_OUT_THRESHOLD = 8
#: How far past its baseline a span must go to be reported.
BASELINE_MULTIPLIER = 2.0


@dataclass
class Finding:
    kind: FindingKind
    summary: str
    confidence: float
    service: str
    span_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "summary": self.summary,
            "confidence": round(self.confidence, 2),
            "service": self.service,
            "span_id": self.span_id,
            "evidence": self.evidence,
        }


def _error_findings(tree: TraceTree) -> list[Finding]:
    findings = []
    for node in tree.nodes:
        if node.span.is_error:
            findings.append(
                Finding(
                    kind=FindingKind.ERROR_SPAN,
                    summary=(
                        f"{node.span.service} failed in {node.span.operation} "
                        f"({node.span.status})"
                    ),
                    # An explicit error status is a fact, not an inference.
                    confidence=0.95,
                    service=node.span.service,
                    span_id=node.span.span_id,
                    evidence={
                        "status": node.span.status,
                        "duration_ms": node.span.duration_ms,
                        "error": node.span.attributes.get("error.message", ""),
                    },
                )
            )
    return findings


def _slow_span_findings(tree: TraceTree) -> list[Finding]:
    if tree.total_duration_ms <= 0:
        return []

    findings = []
    for node in tree.nodes:
        share = node.self_time_ms / tree.total_duration_ms
        if share >= SELF_TIME_SHARE_THRESHOLD and node.self_time_ms >= MIN_SLOW_SPAN_MS:
            findings.append(
                Finding(
                    kind=FindingKind.SLOW_SPAN,
                    summary=(
                        f"{node.span.service} spent {node.self_time_ms:.0f} ms of the "
                        f"{tree.total_duration_ms:.0f} ms trace in {node.span.operation}"
                    ),
                    confidence=min(0.9, 0.5 + share),
                    service=node.span.service,
                    span_id=node.span.span_id,
                    evidence={
                        "self_time_ms": round(node.self_time_ms, 3),
                        "duration_ms": node.span.duration_ms,
                        "share_of_trace": round(share, 3),
                    },
                )
            )
    return findings


def _baseline_findings(tree: TraceTree, baselines: dict[str, float]) -> list[Finding]:
    """A span is only slow relative to what that service normally does."""
    findings = []
    for node in tree.nodes:
        baseline = baselines.get(node.span.service)
        if not baseline or baseline <= 0:
            continue

        ratio = node.span.duration_ms / baseline
        if ratio >= BASELINE_MULTIPLIER:
            findings.append(
                Finding(
                    kind=FindingKind.BASELINE_BREACH,
                    summary=(
                        f"{node.span.service} took {node.span.duration_ms:.0f} ms against a "
                        f"{baseline:.0f} ms p95 baseline ({ratio:.1f}x)"
                    ),
                    confidence=min(0.95, 0.55 + ratio / 20),
                    service=node.span.service,
                    span_id=node.span.span_id,
                    evidence={
                        "duration_ms": node.span.duration_ms,
                        "baseline_p95_ms": baseline,
                        "ratio": round(ratio, 2),
                    },
                )
            )
    return findings


def _retry_findings(tree: TraceTree) -> list[Finding]:
    findings = []
    for node in tree.nodes:
        if len(node.children) < RETRY_THRESHOLD:
            continue

        counts = Counter((c.span.service, c.span.operation) for c in node.children)
        for (service, operation), count in counts.items():
            if count >= RETRY_THRESHOLD:
                findings.append(
                    Finding(
                        kind=FindingKind.RETRY_STORM,
                        summary=(
                            f"{service}.{operation} was called {count} times under one "
                            f"{node.span.operation} span"
                        ),
                        confidence=min(0.85, 0.4 + 0.1 * count),
                        service=service,
                        span_id=node.span.span_id,
                        evidence={"call_count": count, "parent_operation": node.span.operation},
                    )
                )
    return findings


def _cache_findings(tree: TraceTree) -> list[Finding]:
    misses = [
        node for node in tree.nodes if node.span.attributes.get("cache.hit", "").lower() == "false"
    ]
    if not misses:
        return []

    slowest = max(misses, key=lambda n: n.span.duration_ms)
    return [
        Finding(
            kind=FindingKind.CACHE_MISS,
            summary=(
                f"{slowest.span.service} missed cache on {slowest.span.operation}, "
                f"costing {slowest.span.duration_ms:.0f} ms"
            ),
            # A cache miss is a contributing factor far more often than a cause.
            confidence=0.5,
            service=slowest.span.service,
            span_id=slowest.span.span_id,
            evidence={"cache_misses": len(misses), "duration_ms": slowest.span.duration_ms},
        )
    ]


def _structural_findings(tree: TraceTree) -> list[Finding]:
    findings = []

    if tree.orphan_count:
        services = sorted({n.span.service for n in tree.nodes if n.orphaned})
        findings.append(
            Finding(
                kind=FindingKind.MISSING_SPANS,
                summary=(
                    f"{tree.orphan_count} span(s) reference a parent that never arrived — "
                    "part of this trace is missing"
                ),
                confidence=0.6,
                service=services[0] if services else "",
                evidence={"orphan_count": tree.orphan_count, "services": services},
            )
        )

    for node in tree.nodes:
        if len(node.children) >= FAN_OUT_THRESHOLD:
            findings.append(
                Finding(
                    kind=FindingKind.FAN_OUT,
                    summary=(
                        f"{node.span.service}.{node.span.operation} fanned out to "
                        f"{len(node.children)} child calls"
                    ),
                    confidence=0.45,
                    service=node.span.service,
                    span_id=node.span.span_id,
                    evidence={"child_count": len(node.children)},
                )
            )

    return findings


#: An operator reads the first line, so the order is by *kind* first and
#: confidence second. A confident latency observation must never outrank an
#: outright failure, however sure the analyser is about the latency.
KIND_PRIORITY: dict[FindingKind, int] = {
    FindingKind.ERROR_SPAN: 0,
    FindingKind.BASELINE_BREACH: 1,
    FindingKind.SLOW_SPAN: 2,
    FindingKind.RETRY_STORM: 3,
    FindingKind.MISSING_SPANS: 4,
    FindingKind.FAN_OUT: 5,
    FindingKind.CACHE_MISS: 6,
}


def analyse(tree: TraceTree, baselines: dict[str, float] | None = None) -> list[Finding]:
    """Rank findings for one trace: failures first, then measured latency."""
    baselines = baselines or {}

    findings = [
        *_error_findings(tree),
        *_baseline_findings(tree, baselines),
        *_slow_span_findings(tree),
        *_retry_findings(tree),
        *_cache_findings(tree),
        *_structural_findings(tree),
    ]

    findings.sort(key=lambda f: (KIND_PRIORITY.get(f.kind, 99), -f.confidence))
    return findings


def summarise(findings: list[Finding]) -> str:
    """One line for someone who will not read the list."""
    if not findings:
        return "No root cause identified: the trace looks unremarkable."
    return findings[0].summary


def slowest_service(tree: TraceTree) -> tuple[str, float] | None:
    """The service holding the most self time — the default place to look."""
    nodes: list[SpanNode] = tree.nodes
    if not nodes:
        return None

    totals: dict[str, float] = {}
    for node in nodes:
        totals[node.span.service] = totals.get(node.span.service, 0.0) + node.self_time_ms

    service = max(totals, key=lambda name: totals[name])
    return service, round(totals[service], 3)
