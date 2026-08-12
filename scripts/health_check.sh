#!/usr/bin/env bash
# Check every component of the local stack, printing one line each.
#
# Exits non-zero if anything is down, so it works as a gate in CI and in the
# e2e suite as well as being readable by a human.
set -uo pipefail

# Read .env so the script works both from `make` and on its own.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
fi

CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"
PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
GRAFANA_PORT="${GRAFANA_PORT:-3001}"
JAEGER_UI_PORT="${JAEGER_UI_PORT:-16686}"
KAFKA_UI_PORT="${KAFKA_UI_PORT:-8080}"
COLLECTOR_PORT="${COLLECTOR_PORT:-8001}"
API_GATEWAY_PORT="${API_GATEWAY_PORT:-8000}"
DEBUG_SERVICE_PORT="${DEBUG_SERVICE_PORT:-8003}"
METRICS_ENGINE_PORT="${METRICS_ENGINE_PORT:-8002}"

GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
failures=0

# check <name> <url-or-command-marker> [expected-substring]
check_http() {
    local name="$1" url="$2" expect="${3:-}"
    local body
    if body=$(curl -fsS --max-time 5 "$url" 2>/dev/null); then
        if [[ -z "$expect" || "$body" == *"$expect"* ]]; then
            printf '  %s✓%s %-22s %s%s%s\n' "$GREEN" "$RESET" "$name" "$DIM" "$url" "$RESET"
            return 0
        fi
        printf '  %s✗%s %-22s %sunexpected response from %s%s\n' "$RED" "$RESET" "$name" "$DIM" "$url" "$RESET"
    else
        printf '  %s✗%s %-22s %sno response from %s%s\n' "$RED" "$RESET" "$name" "$DIM" "$url" "$RESET"
    fi
    failures=$((failures + 1))
    return 1
}

check_container() {
    local name="$1" container="$2" command="$3"
    if docker exec "$container" sh -c "$command" >/dev/null 2>&1; then
        printf '  %s✓%s %-22s %s%s%s\n' "$GREEN" "$RESET" "$name" "$DIM" "$container" "$RESET"
        return 0
    fi
    printf '  %s✗%s %-22s %s%s not responding%s\n' "$RED" "$RESET" "$name" "$DIM" "$container" "$RESET"
    failures=$((failures + 1))
    return 1
}

# Optional services are reported but do not fail the check until the issue that
# adds them has landed.
#
# The expected marker matters: ports like 8000 are popular, and an unrelated
# service answering on one would otherwise be reported as ours.
check_optional() {
    local name="$1" url="$2" expect="$3"
    local body
    if body=$(curl -fsS --max-time 3 "$url" 2>/dev/null); then
        if [[ "$body" == *"$expect"* ]]; then
            printf '  %s✓%s %-22s %s%s%s\n' "$GREEN" "$RESET" "$name" "$DIM" "$url" "$RESET"
        else
            printf '  %s!%s %-22s %sport %s answered, but not this service%s\n' \
                "$RED" "$RESET" "$name" "$DIM" "${url##*:}" "$RESET"
        fi
    else
        printf '  %s·%s %-22s %snot deployed yet%s\n' "$DIM" "$RESET" "$name" "$DIM" "$RESET"
    fi
}

echo
echo "Infrastructure"
check_container "Kafka"         search-metrics-kafka \
    "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server 127.0.0.1:9092"
check_http      "ClickHouse"    "http://localhost:${CLICKHOUSE_PORT}/ping" "Ok"
check_container "PostgreSQL"    search-metrics-postgres "pg_isready -U \${POSTGRES_USER:-search}"
check_container "Redis"         search-metrics-redis    "redis-cli ping"

echo
echo "Observability"
check_http "Prometheus"  "http://localhost:${PROMETHEUS_PORT}/-/healthy"
check_http "Grafana"     "http://localhost:${GRAFANA_PORT}/api/health" "database"
check_http "Jaeger"      "http://localhost:${JAEGER_UI_PORT}/"
check_http "Kafka UI"    "http://localhost:${KAFKA_UI_PORT}/actuator/health"

echo
echo "Platform services"
check_http     "Telemetry collector" "http://localhost:${COLLECTOR_PORT}/health" "telemetry-collector"
check_optional "Metrics engine"      "http://localhost:${METRICS_ENGINE_PORT}/health" "metrics-engine"
check_optional "Debug service"       "http://localhost:${DEBUG_SERVICE_PORT}/health" "debug-service"
check_optional "API gateway"         "http://localhost:${API_GATEWAY_PORT}/health" "api-gateway"

echo
if (( failures == 0 )); then
    printf '%sAll required components are healthy.%s\n\n' "$GREEN" "$RESET"
    exit 0
fi
printf '%s%d component(s) unhealthy.%s Try: make logs\n\n' "$RED" "$failures" "$RESET"
exit 1
