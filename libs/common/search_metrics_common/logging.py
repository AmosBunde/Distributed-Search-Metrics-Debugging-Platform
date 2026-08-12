"""Structured logging that a log line can be traced from.

Every record is emitted as one JSON object carrying the active trace and span
ids, so a log line found in an aggregator links straight back to the trace that
produced it — which is the entire point of running a debugging platform.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

#: Attributes present on every LogRecord; anything else the caller attached with
#: `extra=` is treated as structured context and included in the output.
_STANDARD_ATTRIBUTES = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    }
)  # fmt: skip


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON line with trace correlation."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = format(context.trace_id, "032x")
            payload["span_id"] = format(context.span_id, "016x")

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str = "INFO") -> logging.Logger:
    """Install the JSON formatter as the only handler on the root logger.

    Replaces existing handlers rather than adding to them, so a service that is
    started twice in one process (tests, reloaders) does not log twice.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # These libraries log a line per request or per poll; useful at DEBUG, noise
    # at INFO.
    for noisy in ("aiokafka", "uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(max(root.level, logging.WARNING))

    return logging.getLogger(service)
