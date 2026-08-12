"""Fixtures shared by the integration and e2e suites.

Both suites talk to the services started by `make dev`, and both skip — rather
than fail — when the stack is not running, so `pytest tests/` on a laptop with
nothing up is quiet rather than a wall of red.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stack_helpers import (
    COLLECTOR_URL,
    DEBUG_URL,
    ENGINE_URL,
    GATEWAY_URL,
    service_is_up,
)


@pytest.fixture(scope="session")
def stack() -> dict[str, str]:
    """Skip the suite unless every service it needs is actually answering."""
    required = {
        "telemetry-collector": (COLLECTOR_URL, "telemetry-collector"),
        "api-gateway": (GATEWAY_URL, "api-gateway"),
        "debug-service": (DEBUG_URL, "debug-service"),
        "metrics-engine": (ENGINE_URL, "metrics-engine"),
    }
    missing = [name for name, (url, marker) in required.items() if not service_is_up(url, marker)]

    if missing:
        pytest.skip(f"stack not running ({', '.join(missing)} unreachable) — try: make dev")

    return {
        "collector": COLLECTOR_URL,
        "gateway": GATEWAY_URL,
        "debug": DEBUG_URL,
        "engine": ENGINE_URL,
    }


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    with httpx.Client(timeout=30.0, headers={"x-client-id": "platform-tests"}) as session:
        yield session
