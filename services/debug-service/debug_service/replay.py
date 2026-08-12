"""Replaying a recorded query and diffing the result.

Replay answers one question an operator cannot answer from a trace alone: does
this still happen? A trace shows what went wrong once; a replay shows whether it
goes wrong now.

Replay is always explicit — it re-issues a real query against a real service, so
it is never triggered automatically by analysis.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

#: Latency this much worse than the original counts as a regression rather than
#: noise; anything less is within normal run-to-run variation.
LATENCY_REGRESSION_RATIO = 1.5


class ReplayStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReplayRequest(BaseModel):
    """What the dashboard sends to re-run a query."""

    query_id: str = Field(min_length=1, max_length=128)
    target_service: str | None = Field(default=None, max_length=64)
    requested_by: str | None = Field(default=None, max_length=128)


@dataclass
class QueryRun:
    """One execution of a query — either the original or the replay."""

    query: str
    latency_ms: float
    result_count: int
    status: str = "ok"
    document_ids: list[str] = field(default_factory=list)


@dataclass
class ReplayDiff:
    """How the replay differed from the recorded run."""

    latency_delta_ms: float
    latency_ratio: float
    result_count_delta: int
    results_match: bool
    common_documents: int
    added_documents: list[str]
    removed_documents: list[str]
    status_changed: bool
    verdict: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "latency_delta_ms": round(self.latency_delta_ms, 3),
            "latency_ratio": round(self.latency_ratio, 3),
            "result_count_delta": self.result_count_delta,
            "results_match": self.results_match,
            "common_documents": self.common_documents,
            "added_documents": self.added_documents,
            "removed_documents": self.removed_documents,
            "status_changed": self.status_changed,
            "verdict": self.verdict,
        }


def diff_runs(original: QueryRun, replay: QueryRun) -> ReplayDiff:
    """Compare two runs of the same query.

    Result comparison is by document set, not by rank: a ranking change is worth
    knowing about but is not the same as returning different documents, and
    comparing ordered lists would report every minor score shuffle as a
    difference.
    """
    original_docs = set(original.document_ids)
    replay_docs = set(replay.document_ids)

    latency_ratio = replay.latency_ms / original.latency_ms if original.latency_ms > 0 else 1.0
    status_changed = original.status != replay.status
    results_match = original_docs == replay_docs

    if status_changed and replay.status != "ok":
        verdict = "still failing"
    elif status_changed and replay.status == "ok":
        verdict = "no longer reproducible"
    elif latency_ratio >= LATENCY_REGRESSION_RATIO:
        verdict = "slower than the original run"
    elif not results_match:
        verdict = "different results"
    else:
        verdict = "matches the original run"

    return ReplayDiff(
        latency_delta_ms=replay.latency_ms - original.latency_ms,
        latency_ratio=latency_ratio,
        result_count_delta=replay.result_count - original.result_count,
        results_match=results_match,
        common_documents=len(original_docs & replay_docs),
        added_documents=sorted(replay_docs - original_docs),
        removed_documents=sorted(original_docs - replay_docs),
        status_changed=status_changed,
        verdict=verdict,
    )


@dataclass
class ReplayJob:
    """A replay request and, once it has run, its outcome."""

    id: uuid.UUID
    query_id: str
    target_service: str
    status: ReplayStatus = ReplayStatus.PENDING
    requested_by: str | None = None
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    original: QueryRun | None = None
    replay: QueryRun | None = None
    diff: ReplayDiff | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "query_id": self.query_id,
            "target_service": self.target_service,
            "status": str(self.status),
            "requested_by": self.requested_by,
            "requested_at": self.requested_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "original": _run_as_dict(self.original),
            "replay": _run_as_dict(self.replay),
            "diff": self.diff.as_dict() if self.diff else None,
            "error": self.error,
        }


def _run_as_dict(run: QueryRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "query": run.query,
        "latency_ms": run.latency_ms,
        "result_count": run.result_count,
        "status": run.status,
        "document_ids": run.document_ids,
    }


async def execute_replay(job: ReplayJob, original: QueryRun, executor: Any) -> ReplayJob:
    """Run the replay through `executor` and record the comparison.

    A failure of the *replay mechanism* is recorded on the job rather than
    raised: the operator asked a question, and "the target refused the
    connection" is an answer.
    """
    job.status = ReplayStatus.RUNNING
    job.original = original

    try:
        job.replay = await executor.run(original.query, job.target_service)
        job.diff = diff_runs(original, job.replay)
        job.status = ReplayStatus.SUCCEEDED
    except Exception as exc:
        job.status = ReplayStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.completed_at = datetime.now(UTC)

    return job
