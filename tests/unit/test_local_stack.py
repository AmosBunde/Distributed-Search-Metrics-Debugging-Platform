"""The local stack is configuration, and configuration rots quietly.

These tests catch the failures that would otherwise only appear when someone
runs `make dev` on a clean machine: a bind mount pointing at a file that was
renamed, a service with no healthcheck (so `--wait` returns before it is ready),
a compose variable missing from `.env.example`, or a scrape target that no
longer matches a service name.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def env_example_keys() -> set[str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    }


def test_compose_file_is_valid_yaml(compose: dict) -> None:
    assert compose["name"] == "search-metrics"
    assert compose["services"]


EXPECTED_SERVICES = {
    "kafka",
    "kafka-init",
    "kafka-ui",
    "clickhouse",
    "postgres",
    "redis",
    "jaeger",
    "prometheus",
    "grafana",
    "telemetry-collector",
    "metrics-engine",
    "debug-service",
    "api-gateway",
    "query-simulator",
    "dashboard",
}


def test_every_expected_service_is_defined(compose: dict) -> None:
    assert set(compose["services"]) >= EXPECTED_SERVICES


def test_long_running_services_have_healthchecks(compose: dict) -> None:
    """`docker compose up --wait` only converges on services that report health.

    A built service may declare its check in its Dockerfile instead of here;
    either is fine, having neither is not.
    """
    one_shot = {"kafka-init", "query-simulator"}
    missing = []

    for name, service in compose["services"].items():
        if name in one_shot or "healthcheck" in service:
            continue
        dockerfile = service.get("build", {}).get("dockerfile")
        if dockerfile and "HEALTHCHECK" in (ROOT / dockerfile).read_text(encoding="utf-8"):
            continue
        missing.append(name)

    assert not missing, f"services without a healthcheck: {missing}"


def test_healthchecks_use_ipv4_loopback(compose: dict) -> None:
    """`localhost` can resolve to ::1 on a host without IPv6, where nothing listens.

    This cost a debugging session: ClickHouse bound 0.0.0.0 only, and its
    healthcheck failed against ::1 while the server was perfectly healthy.
    """
    for name, service in compose["services"].items():
        test = service.get("healthcheck", {}).get("test", [])
        joined = " ".join(test) if isinstance(test, list) else str(test)
        assert "localhost" not in joined, f"{name} healthcheck should use 127.0.0.1"


def test_bind_mounts_point_at_files_that_exist(compose: dict) -> None:
    for name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            if not volume.startswith("./"):
                continue  # named volume
            host_path = ROOT / volume.split(":", 1)[0].lstrip("./")
            assert host_path.exists(), f"{name} mounts missing path: {host_path}"


def test_dependencies_wait_for_health(compose: dict) -> None:
    """A plain `depends_on` only waits for start, which races on a cold stack."""
    for name, service in compose["services"].items():
        depends = service.get("depends_on", {})
        if isinstance(depends, dict):
            for dependency, condition in depends.items():
                assert condition["condition"] in {
                    "service_healthy",
                    "service_completed_successfully",
                }, f"{name} -> {dependency} does not wait for readiness"


def test_compose_variables_are_documented_in_env_example(
    compose: dict, env_example_keys: set[str]
) -> None:
    """Anything configurable must appear in the file adopters actually copy."""
    import re

    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    referenced = set(re.findall(r"\$\{([A-Z0-9_]+)(?::-[^}]*)?\}", raw))
    undocumented = referenced - env_example_keys
    assert not undocumented, f"used in compose but missing from .env.example: {undocumented}"


def test_prometheus_scrapes_every_platform_service() -> None:
    config = yaml.safe_load(
        (ROOT / "docker" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    )
    targets = {
        target
        for job in config["scrape_configs"]
        for entry in job.get("static_configs", [])
        for target in entry["targets"]
    }
    for service in ("telemetry-collector:8001", "metrics-engine:8002", "api-gateway:8000"):
        assert service in targets


def test_grafana_datasources_are_provisioned() -> None:
    path = ROOT / "docker" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = {datasource["name"] for datasource in config["datasources"]}
    assert {"Prometheus", "Jaeger"} <= names


class TestSchemas:
    def test_clickhouse_schema_creates_every_table_the_platform_reads(self) -> None:
        sql = (ROOT / "scripts" / "clickhouse" / "init.sql").read_text(encoding="utf-8")
        for table in ("events", "query_results", "metric_rollups", "anomalies", "spans"):
            assert f"search_metrics.{table}" in sql

    def test_rollups_are_replacing_so_reprocessing_is_idempotent(self) -> None:
        """At-least-once delivery is only safe if a rewritten window replaces itself."""
        sql = (ROOT / "scripts" / "clickhouse" / "init.sql").read_text(encoding="utf-8")
        rollups = sql[sql.index("metric_rollups") :]
        assert "ReplacingMergeTree" in rollups.split(";")[0]

    def test_raw_telemetry_expires(self) -> None:
        sql = (ROOT / "scripts" / "clickhouse" / "init.sql").read_text(encoding="utf-8")
        assert "TTL toDateTime(timestamp) + INTERVAL 90 DAY" in sql

    def test_postgres_schema_covers_mutable_state(self) -> None:
        sql = (ROOT / "scripts" / "postgres" / "init.sql").read_text(encoding="utf-8")
        for table in ("services", "replay_jobs", "alert_state"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    def test_alert_state_is_keyed_by_signature_for_deduplication(self) -> None:
        sql = (ROOT / "scripts" / "postgres" / "init.sql").read_text(encoding="utf-8")
        assert "signature     TEXT PRIMARY KEY" in sql


class TestMakefile:
    @pytest.fixture(scope="class")
    def makefile(self) -> str:
        return (ROOT / "Makefile").read_text(encoding="utf-8")

    def test_env_is_not_exported_into_child_processes(self, makefile: str) -> None:
        """Exporting .env made the test suite assert against local configuration."""
        include_block = makefile[makefile.index("ifneq (,$(wildcard .env))") :][:200]
        assert "\nexport\n" not in include_block

    @pytest.mark.parametrize(
        "target", ["dev", "down", "logs", "health", "check-kafka", "check-metrics"]
    )
    def test_stack_targets_are_implemented(self, target: str, makefile: str) -> None:
        body = makefile[makefile.index(f"\n{target}:") :].split("\n\n")[0]
        assert "not_implemented" not in body, f"{target} is still a stub"


def test_service_packages_have_distinct_names() -> None:
    """Two services both naming their package `app` collide in one pytest session.

    The first `app` imported wins and every other service's tests fail with a
    confusing ModuleNotFoundError, so each service names its package after
    itself.
    """
    packages = []
    for service in (ROOT / "services").iterdir():
        if not service.is_dir():
            continue
        packages += [child.name for child in service.iterdir() if (child / "__init__.py").is_file()]

    assert "app" not in packages, "a service package named `app` will collide"
    assert len(packages) == len(set(packages)), f"duplicate service packages: {packages}"


class TestContinuousIntegration:
    """CI is only useful if it runs the same checks a developer runs locally."""

    @pytest.fixture(scope="class")
    def ci(self) -> dict:
        return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))

    def test_the_pipeline_lints_tests_and_builds(self, ci: dict) -> None:
        assert {"lint", "unit-tests", "dashboard", "images", "integration"} <= set(ci["jobs"])

    def test_the_coverage_gate_matches_the_makefile(self, ci: dict) -> None:
        """A threshold CI enforces but `make coverage` does not is a trap."""
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert f"COVERAGE_MIN ?= {ci['env']['COVERAGE_MIN']}" in makefile

    def test_every_service_image_is_built(self, ci: dict) -> None:
        built = set(ci["jobs"]["images"]["strategy"]["matrix"]["service"])
        services = {
            path.name for path in (ROOT / "services").iterdir() if (path / "Dockerfile").is_file()
        }
        assert services <= built, f"images never built in CI: {services - built}"

    def test_permissions_are_least_privilege(self, ci: dict) -> None:
        assert ci["permissions"] == {"contents": "read"}

    def test_in_flight_runs_are_superseded(self, ci: dict) -> None:
        assert ci["concurrency"]["cancel-in-progress"] is True

    def test_the_integration_job_waits_for_the_cheap_ones(self, ci: dict) -> None:
        assert set(ci["jobs"]["integration"]["needs"]) == {"lint", "unit-tests"}

    def test_the_integration_job_captures_logs_on_failure(self, ci: dict) -> None:
        """A red CI run with no logs costs another round trip to diagnose."""
        steps = ci["jobs"]["integration"]["steps"]
        assert any(step.get("if") == "failure()" and "logs" in str(step) for step in steps)

    def test_release_publishes_every_service(self) -> None:
        release = yaml.safe_load(
            (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        )
        built = set(release["jobs"]["publish"]["strategy"]["matrix"]["service"])
        services = {
            path.name for path in (ROOT / "services").iterdir() if (path / "Dockerfile").is_file()
        }
        assert services <= built
