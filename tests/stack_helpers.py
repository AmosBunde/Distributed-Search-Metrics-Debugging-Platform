"""Helpers for tests that need the real stack.

These tests talk to the services started by `make dev`. They are skipped — not
failed — when the stack is not running, so `pytest tests/` on a laptop with
nothing up is quiet rather than a wall of red.

Everything here polls with a timeout instead of sleeping a fixed amount. A fixed
sleep is either too short (flaky on a loaded machine) or too long (slow for
everyone), and it hides how long the pipeline actually takes.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]

T = TypeVar("T")


def _env(name: str, default: str) -> str:
    """Read configuration from .env so the suite follows the ports in use."""
    if name in os.environ:
        return os.environ[name]

    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return default


COLLECTOR_URL = f"http://localhost:{_env('COLLECTOR_PORT', '8001')}"
GATEWAY_URL = f"http://localhost:{_env('API_GATEWAY_PORT', '8000')}"
DEBUG_URL = f"http://localhost:{_env('DEBUG_SERVICE_PORT', '8003')}"
ENGINE_URL = f"http://localhost:{_env('METRICS_ENGINE_PORT', '8002')}"
CLICKHOUSE_URL = f"http://localhost:{_env('CLICKHOUSE_PORT', '8123')}"
CLICKHOUSE_AUTH = (_env("CLICKHOUSE_USER", "search"), _env("CLICKHOUSE_PASSWORD", "changeme"))
CLICKHOUSE_DB = _env("CLICKHOUSE_DB", "search_metrics")


def service_is_up(url: str, expected: str) -> bool:
    try:
        response = httpx.get(f"{url}/health", timeout=3.0)
        return response.status_code == 200 and expected in response.text
    except httpx.HTTPError:
        return False


def eventually(
    probe: Callable[[], T],
    predicate: Callable[[T], bool] = bool,
    timeout: float = 60.0,
    interval: float = 1.0,
    description: str = "condition",
) -> T:
    """Poll until the predicate holds, then return the value.

    The pipeline is asynchronous by design — ingest, Kafka, a window closing, a
    batched insert — so an assertion straight after a write would be testing
    luck.
    """
    deadline = time.monotonic() + timeout
    last: Any = None

    while time.monotonic() < deadline:
        last = probe()
        if predicate(last):
            return last
        time.sleep(interval)

    raise AssertionError(
        f"{description} did not happen within {timeout:.0f}s; last value was {last!r}"
    )


def clickhouse(sql: str) -> list[list[str]]:
    """Run a query and return TSV rows."""
    response = httpx.post(
        CLICKHOUSE_URL,
        params={"database": CLICKHOUSE_DB},
        content=f"{sql} FORMAT TSV".encode(),
        auth=CLICKHOUSE_AUTH,
        timeout=20.0,
    )
    response.raise_for_status()
    return [line.split("\t") for line in response.text.strip().splitlines() if line]


def clickhouse_count(table: str, where: str = "1") -> int:
    rows = clickhouse(f"SELECT count() FROM {table} WHERE {where}")
    return int(rows[0][0]) if rows else 0
