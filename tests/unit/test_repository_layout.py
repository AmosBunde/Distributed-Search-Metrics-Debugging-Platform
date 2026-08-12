"""The repository skeleton is a contract other issues build on.

These tests are deliberately cheap: they catch a directory or entry-point file
being renamed or deleted without the rest of the repository being updated.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DIRECTORIES = [
    "libs/common",
    "services/telemetry-collector",
    "services/metrics-engine",
    "services/debug-service",
    "services/api-gateway",
    "services/query-simulator",
    "services/dashboard",
    "infrastructure/terraform/modules",
    "infrastructure/terraform/environments",
    "helm",
    "scripts",
    "docs",
    "tests/unit",
    "tests/integration",
    "tests/e2e",
]

REQUIRED_FILES = [
    ".env.example",
    ".gitignore",
    "Makefile",
    "pyproject.toml",
    "requirements-dev.txt",
    "CONTRIBUTING.md",
    "README.md",
]


@pytest.mark.parametrize("relative", REQUIRED_DIRECTORIES)
def test_required_directory_exists(relative: str) -> None:
    assert (ROOT / relative).is_dir(), f"missing directory: {relative}"


@pytest.mark.parametrize("relative", REQUIRED_FILES)
def test_required_file_exists(relative: str) -> None:
    assert (ROOT / relative).is_file(), f"missing file: {relative}"


def test_env_example_documents_every_referenced_setting() -> None:
    """`.env.example` is the single source of truth for configuration."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    keys = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    }
    expected = {
        "ENVIRONMENT",
        "LOG_LEVEL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_TOPIC_EVENTS",
        "KAFKA_TOPIC_ERRORS",
        "KAFKA_TOPIC_RESULTS",
        "KAFKA_TOPIC_ANOMALIES",
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_DB",
        "POSTGRES_HOST",
        "POSTGRES_DB",
        "REDIS_HOST",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "API_GATEWAY_PORT",
        "COLLECTOR_PORT",
    }
    assert expected <= keys, f"missing from .env.example: {sorted(expected - keys)}"


def test_env_example_contains_no_real_looking_secrets() -> None:
    """Placeholders only — a committed example must never carry a usable secret."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(token in key for token in ("PASSWORD", "SECRET", "TOKEN", "KEY")):
            assert value.strip() in {
                "",
                "changeme",
                "admin",
            }, f"{key} must be a placeholder in .env.example, got {value!r}"


def test_makefile_documents_every_public_target() -> None:
    """`make help` is only useful if every target carries a description."""
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    phony = {
        target
        for line in text.splitlines()
        if line.startswith(".PHONY:")
        for target in line.replace(".PHONY:", "", 1).split()
    }
    documented = {
        line.split(":", 1)[0].strip() for line in text.splitlines() if ":" in line and "## " in line
    }
    assert phony, "no .PHONY targets found — did the Makefile format change?"
    assert phony <= documented, f"undocumented targets: {sorted(phony - documented)}"
