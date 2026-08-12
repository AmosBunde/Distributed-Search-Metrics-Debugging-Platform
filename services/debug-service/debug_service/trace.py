"""Assembling a distributed trace from a bag of spans.

Spans arrive in whatever order the storage returns them, from services that did
not coordinate, and the set is not guaranteed to be complete — a service may
have failed to export, or its spans may not have arrived yet. The tree builder
therefore has to be tolerant of missing parents and hostile to cycles: a cycle
would hang the renderer, and an orphan dropped silently would hide the very hop
someone is investigating.

Self time — a span's duration minus the time its children were running — is what
actually points at the culprit. A ten second parent whose child took 9.9 seconds
is not slow; its child is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Span(BaseModel):
    """One operation in one service."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str = ""
    query_id: str = ""
    service: str
    operation: str
    start_time: datetime
    duration_ms: float = Field(ge=0)
    status: str = "ok"
    attributes: dict[str, str] = Field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.status.lower() not in {"ok", "unset", ""}


@dataclass
class SpanNode:
    """A span with its place in the tree."""

    span: Span
    depth: int = 0
    children: list[SpanNode] = field(default_factory=list)
    orphaned: bool = False

    @property
    def self_time_ms(self) -> float:
        """Time spent in this span rather than waiting on its children."""
        return max(0.0, self.span.duration_ms - sum(c.span.duration_ms for c in self.children))

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class TraceTree:
    trace_id: str
    roots: list[SpanNode]
    span_count: int
    orphan_count: int
    total_duration_ms: float
    services: list[str]

    @property
    def nodes(self) -> list[SpanNode]:
        return [node for root in self.roots for node in root.walk()]

    def critical_path(self) -> list[SpanNode]:
        """The chain of longest children — where the time actually went."""
        if not self.roots:
            return []

        node = max(self.roots, key=lambda n: n.span.duration_ms)
        path = [node]
        while node.children:
            node = max(node.children, key=lambda c: c.span.duration_ms)
            path.append(node)
        return path

    def as_dict(self) -> dict[str, Any]:
        def render(node: SpanNode) -> dict[str, Any]:
            return {
                "span_id": node.span.span_id,
                "parent_span_id": node.span.parent_span_id,
                "service": node.span.service,
                "operation": node.span.operation,
                "start_time": node.span.start_time.isoformat(),
                "duration_ms": node.span.duration_ms,
                "self_time_ms": round(node.self_time_ms, 3),
                "status": node.span.status,
                "depth": node.depth,
                "orphaned": node.orphaned,
                "attributes": node.span.attributes,
                "children": [render(child) for child in node.children],
            }

        return {
            "trace_id": self.trace_id,
            "span_count": self.span_count,
            "orphan_count": self.orphan_count,
            "total_duration_ms": self.total_duration_ms,
            "services": self.services,
            "roots": [render(root) for root in self.roots],
        }


class CyclicTraceError(ValueError):
    """A span graph that contains a cycle is corrupt, not merely unusual."""


def build_trace(spans: list[Span]) -> TraceTree:
    """Assemble spans into a tree, ordered by start time at every level.

    A span whose parent is not in the set is kept as an additional root and
    marked `orphaned`, because the missing parent is usually the interesting
    part — a service that never reported is exactly what an investigation is
    looking for.
    """
    if not spans:
        return TraceTree("", [], 0, 0, 0.0, [])

    by_id = {span.span_id: span for span in spans}
    _reject_cycles(by_id)

    nodes = {span.span_id: SpanNode(span=span) for span in spans}
    roots: list[SpanNode] = []
    orphans = 0

    for span in spans:
        node = nodes[span.span_id]
        parent_id = span.parent_span_id

        if not parent_id:
            roots.append(node)
        elif parent_id in nodes:
            nodes[parent_id].children.append(node)
        else:
            node.orphaned = True
            orphans += 1
            roots.append(node)

    for root in roots:
        _assign_depth(root, 0)

    roots.sort(key=lambda n: n.span.start_time)
    for node in (n for root in roots for n in root.walk()):
        node.children.sort(key=lambda c: c.span.start_time)

    starts = [span.start_time for span in spans]
    ends = [span.start_time.timestamp() + span.duration_ms / 1000.0 for span in spans]
    total = (max(ends) - min(starts).timestamp()) * 1000.0

    return TraceTree(
        trace_id=spans[0].trace_id,
        roots=roots,
        span_count=len(spans),
        orphan_count=orphans,
        total_duration_ms=round(total, 3),
        services=sorted({span.service for span in spans}),
    )


def _assign_depth(node: SpanNode, depth: int) -> None:
    node.depth = depth
    for child in node.children:
        _assign_depth(child, depth + 1)


def _reject_cycles(by_id: dict[str, Span]) -> None:
    """Walk each span's ancestry; revisiting a span means the graph is corrupt."""
    for span_id in by_id:
        seen = {span_id}
        current = by_id[span_id].parent_span_id

        while current and current in by_id:
            if current in seen:
                raise CyclicTraceError(f"cycle in trace through span {current}")
            seen.add(current)
            current = by_id[current].parent_span_id
