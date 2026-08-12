"""Shared fixtures for the unit suite.

Unit tests must not depend on infrastructure *or* on the developer's machine.
The one sneaky dependency is `.env`: `Settings` reads it from the working
directory, so once a contributor runs `cp .env.example .env` the settings tests
would start asserting against their local values. Running every unit test from
an empty directory removes that coupling.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each unit test in an empty directory, so no local .env is picked up."""
    monkeypatch.chdir(tmp_path)
