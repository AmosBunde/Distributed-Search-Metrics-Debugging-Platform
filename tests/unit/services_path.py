"""Make a service's `app` package importable from the unit tests.

Each service is a self-contained deployable with its own `app` package rather
than an installed library, so tests put the service directory on `sys.path`
before importing it. Only one service's `app` can be imported per test session,
which is why each service has its own test module.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def add_service_to_path(service: str) -> Path:
    """Put `services/<service>` on `sys.path` and return it."""
    directory = REPO_ROOT / "services" / service
    if not directory.is_dir():
        raise RuntimeError(f"no such service: {service}")
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return directory
