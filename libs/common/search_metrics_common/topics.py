"""Kafka topic names and the routing rule that decides between them.

Topic names are configurable so an adopter can prefix them per environment, but
the *routing* — which record goes where — is fixed here so that the collector,
the engine and the debug service cannot disagree about it.
"""

from __future__ import annotations

from enum import StrEnum


class Topic(StrEnum):
    """Logical topics, independent of the configured physical names."""

    EVENTS = "events"
    RESULTS = "results"
    ERRORS = "errors"
    ANOMALIES = "anomalies"


#: Every logical topic the platform uses, in the order they appear in the pipeline.
ALL_TOPICS: tuple[Topic, ...] = (Topic.EVENTS, Topic.RESULTS, Topic.ERRORS, Topic.ANOMALIES)
