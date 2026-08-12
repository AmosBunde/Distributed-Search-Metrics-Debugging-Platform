"""Configuration, read from the environment.

Every setting has a default that works against the `make dev` stack, so a
service starts with no configuration at all in development. Anything secret
defaults to empty rather than to a working value.

The field names mirror `.env.example` one for one; that file is the
documentation for this class.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .topics import Topic


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "local"
    log_level: str = "INFO"
    service_name: str = "search-metrics"

    # Kafka ------------------------------------------------------------------
    kafka_bootstrap_servers: str = "localhost:19092"
    kafka_topic_events: str = "search.events"
    kafka_topic_results: str = "search.results"
    kafka_topic_errors: str = "search.errors"
    kafka_topic_anomalies: str = "search.anomalies"
    kafka_consumer_group: str = "metrics-engine"
    kafka_topic_partitions: int = Field(default=6, ge=1)
    kafka_topic_retention_ms: int = Field(default=604_800_000, ge=0)

    # ClickHouse -------------------------------------------------------------
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_db: str = "search_metrics"
    clickhouse_user: str = "search"
    clickhouse_password: str = ""

    # PostgreSQL -------------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "search_metrics_meta"
    postgres_user: str = "search"
    postgres_password: str = ""

    # Redis ------------------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # Rate limiting ----------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=6_000, ge=1)
    rate_limit_burst: int = Field(default=600, ge=1)
    max_batch_size: int = Field(default=500, ge=1, le=500)

    # Metrics engine ---------------------------------------------------------
    window_seconds: int = Field(default=60, ge=1)
    anomaly_zscore_threshold: float = Field(default=3.0, gt=0)
    anomaly_baseline_windows: int = Field(default=30, ge=2)
    clickhouse_insert_batch_size: int = Field(default=1_000, ge=1)
    clickhouse_insert_interval_seconds: float = Field(default=5.0, gt=0)

    # Tracing ----------------------------------------------------------------
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_traces_sampler_arg: float = Field(default=1.0, ge=0.0, le=1.0)
    otel_sdk_disabled: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def clickhouse_url(self) -> str:
        return f"http://{self.clickhouse_host}:{self.clickhouse_port}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def postgres_dsn(self) -> str:
        credentials = self.postgres_user
        if self.postgres_password:
            credentials = f"{credentials}:{self.postgres_password}"
        return f"postgresql://{credentials}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    def topic_name(self, topic: Topic) -> str:
        """Resolve a logical topic to its configured physical name."""
        return {
            Topic.EVENTS: self.kafka_topic_events,
            Topic.RESULTS: self.kafka_topic_results,
            Topic.ERRORS: self.kafka_topic_errors,
            Topic.ANOMALIES: self.kafka_topic_anomalies,
        }[topic]

    @property
    def bootstrap_servers(self) -> list[str]:
        servers = self.kafka_bootstrap_servers.split(",")
        return [server.strip() for server in servers if server.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so every module sees the same values."""
    return Settings()
