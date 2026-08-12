#!/usr/bin/env bash
# Row counts and freshness in ClickHouse — is data actually flowing?
#
# This is the check to run after `make simulate`: if the counts here are not
# rising, the problem is upstream of the dashboard.
set -uo pipefail

# Read .env so the script works both from `make` and on its own.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
fi

HOST="${CLICKHOUSE_HTTP_HOST:-localhost}"
PORT="${CLICKHOUSE_PORT:-8123}"
USER="${CLICKHOUSE_USER:-search}"
PASSWORD="${CLICKHOUSE_PASSWORD:-changeme}"
DB="${CLICKHOUSE_DB:-search_metrics}"

query() {
    curl -fsS --max-time 10 \
        --user "${USER}:${PASSWORD}" \
        --data-binary "$1" \
        "http://${HOST}:${PORT}/?database=${DB}" 2>/dev/null
}

if ! query "SELECT 1" >/dev/null; then
    echo "ClickHouse is not reachable at ${HOST}:${PORT}. Start the stack with: make dev" >&2
    exit 1
fi

echo
echo "Table                    rows        newest record"
echo "--------------------------------------------------------"
for entry in "events:timestamp" "query_results:timestamp" "metric_rollups:window_start" "anomalies:window_start" "spans:start_time"; do
    table="${entry%%:*}"
    time_column="${entry##*:}"
    row=$(query "SELECT count(), ifNull(toString(max(${time_column})), '—') FROM ${table} FORMAT TSV")
    printf "%-22s %10s   %s\n" "$table" "$(echo "$row" | cut -f1)" "$(echo "$row" | cut -f2)"
done

echo
echo "Events by service (last 15 minutes)"
echo "-----------------------------------"
result=$(query "
    SELECT service, count() AS events, round(avg(latency_ms), 1) AS avg_ms,
           round(100 * countIf(status != 'ok') / count(), 2) AS error_pct
    FROM events
    WHERE timestamp > now() - INTERVAL 15 MINUTE
    GROUP BY service ORDER BY events DESC FORMAT TSV")

if [[ -z "$result" ]]; then
    echo "  (no events in the last 15 minutes — try: make simulate QPS=500)"
else
    printf "%-22s %8s %10s %10s\n" "service" "events" "avg ms" "errors %"
    echo "$result" | while IFS=$'\t' read -r service events avg_ms error_pct; do
        printf "%-22s %8s %10s %10s\n" "$service" "$events" "$avg_ms" "$error_pct"
    done
fi
echo
