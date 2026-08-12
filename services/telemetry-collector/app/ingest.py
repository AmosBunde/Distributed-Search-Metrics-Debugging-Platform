"""Validation, enrichment and publishing — the collector's actual work.

Kept free of FastAPI so it can be tested directly: give it a publisher and a
list of raw dicts, and assert on what came back and what was published.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from search_metrics_common import IngestResult, SearchEvent
from search_metrics_common.tracing import current_trace_id

logger = logging.getLogger(__name__)


def enrich(event: SearchEvent, received_at: datetime | None = None) -> SearchEvent:
    """Attach what the collector knows and the caller could not.

    The receive time is recorded separately from the caller's timestamp so a
    client with a skewed clock is visible rather than silently distorting a
    window. The active trace id is attached when the caller did not supply one,
    which is what later links this event to its distributed trace.
    """
    metadata = dict(event.metadata)
    metadata["received_at"] = (received_at or datetime.now(UTC)).isoformat()

    updates: dict[str, Any] = {"metadata": metadata}
    if not event.trace_id:
        trace_id = current_trace_id()
        if trace_id:
            updates["trace_id"] = trace_id

    return event.model_copy(update=updates)


def _describe(index: int, error: ValidationError) -> str:
    """One readable line per rejected event, naming the field at fault."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "event"
    return f"event {index}: {location}: {first['msg']}"


async def ingest_events(
    raw_events: list[dict[str, Any]],
    publisher: Any,
    received_at: datetime | None = None,
) -> IngestResult:
    """Validate, enrich and publish a batch, reporting per-event failures.

    One malformed event does not reject the batch: valid events are published
    and the caller is told exactly which entries failed and why. A client
    shipping a thousand events an hour should not lose the other 999 because of
    one bad record.
    """
    accepted = 0
    errors: list[str] = []

    for index, raw in enumerate(raw_events):
        try:
            event = SearchEvent.model_validate(raw)
        except ValidationError as exc:
            errors.append(_describe(index, exc))
            continue

        enriched = enrich(event, received_at)
        await publisher.publish_event(enriched)
        await publisher.publish_results(enriched)
        accepted += 1

    if errors:
        logger.warning(
            "batch partially rejected", extra={"accepted": accepted, "rejected": len(errors)}
        )

    return IngestResult(accepted=accepted, rejected=len(errors), errors=errors)
