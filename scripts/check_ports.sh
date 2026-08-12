#!/usr/bin/env bash
# Check that every host port the stack needs is free, before Docker tries.
#
# Without this, the first command an adopter runs fails with:
#
#   Error response from daemon: ... Bind for 0.0.0.0:6379 failed:
#   port is already allocated
#
# which says nothing about which service wanted it or what to change. Every
# port is configurable, so a conflict is a one-line edit — as long as you know
# that, which is the whole point of this script.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
fi

RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

# variable:default:what needs it
PORTS=(
    "API_GATEWAY_PORT:8000:API gateway"
    "COLLECTOR_PORT:8001:Telemetry collector"
    "METRICS_ENGINE_PORT:8002:Metrics engine"
    "DEBUG_SERVICE_PORT:8003:Debug service"
    "DASHBOARD_PORT:3000:Dashboard"
    "GRAFANA_PORT:3001:Grafana"
    "PROMETHEUS_PORT:9090:Prometheus"
    "ALERTMANAGER_PORT:9093:Alertmanager"
    "JAEGER_UI_PORT:16686:Jaeger"
    "KAFKA_UI_PORT:8080:Kafka UI"
    "CLICKHOUSE_PORT:8123:ClickHouse HTTP"
    "CLICKHOUSE_NATIVE_PORT:9000:ClickHouse native"
    "POSTGRES_PORT:5432:PostgreSQL"
    "REDIS_PORT:6379:Redis"
)

port_in_use() {
    local port="$1"
    # Ports held by this project's own containers are not conflicts: `make dev`
    # is expected to be re-runnable.
    if docker ps --filter "name=search-metrics-" --format '{{.Ports}}' 2>/dev/null \
        | grep -qE "(^|[^0-9])${port}->"; then
        return 1
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$"
    else
        (echo >"/dev/tcp/127.0.0.1/${port}") >/dev/null 2>&1
    fi
}

conflicts=()
for entry in "${PORTS[@]}"; do
    variable="${entry%%:*}"
    rest="${entry#*:}"
    default="${rest%%:*}"
    label="${rest#*:}"
    port="${!variable:-$default}"

    if port_in_use "$port"; then
        conflicts+=("${variable}|${port}|${label}")
    fi
done

if [[ ${#conflicts[@]} -eq 0 ]]; then
    printf '%s✓%s every host port is free\n' "$GREEN" "$RESET"
    exit 0
fi

printf '\n%s%d host port(s) are already in use:%s\n\n' "$RED$BOLD" "${#conflicts[@]}" "$RESET"
for conflict in "${conflicts[@]}"; do
    IFS='|' read -r variable port label <<<"$conflict"
    holder=""
    if command -v ss >/dev/null 2>&1; then
        holder=$(ss -tlnp 2>/dev/null | grep -E "[:.]${port}\b" | grep -oE 'users:\(\("[^"]+' | head -1 | cut -d'"' -f2)
    fi
    printf '  %s%-5s%s  %-22s %s\n' "$BOLD" "$port" "$RESET" "$label" \
        "${DIM}${holder:+held by $holder}${RESET}"
done

# Suggest a port that is free *and* not already claimed by another service in
# this stack: suggesting 8001 for the gateway when the collector uses it would
# only move the problem.
claimed=()
for entry in "${PORTS[@]}"; do
    variable="${entry%%:*}"
    rest="${entry#*:}"
    default="${rest%%:*}"
    claimed+=("${!variable:-$default}")
done

suggest() {
    local candidate=$(( $1 + 1 ))
    while :; do
        local taken=0
        for used in "${claimed[@]}"; do
            [[ "$used" == "$candidate" ]] && taken=1 && break
        done
        if [[ $taken -eq 0 ]] && ! port_in_use "$candidate"; then
            claimed+=("$candidate")
            echo "$candidate"
            return
        fi
        candidate=$(( candidate + 1 ))
    done
}

printf '\n%sEvery port is configurable.%s Edit .env and pick free ones:\n\n' "$BOLD" "$RESET"
for conflict in "${conflicts[@]}"; do
    IFS='|' read -r variable port _ <<<"$conflict"
    printf '  %s=%s\n' "$variable" "$(suggest "$port")"
done
printf '\nOnly the host side changes — the services still talk to each other\n'
printf 'on their standard ports inside the compose network.\n\n'
exit 1
