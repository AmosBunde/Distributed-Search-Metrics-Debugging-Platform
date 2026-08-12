#!/usr/bin/env bash
# Topics, partition counts and consumer lag.
#
# Lag is the platform's primary back-pressure signal: if it grows steadily the
# metrics engine cannot keep up with ingest (ADR-0001, ADR-0003).
set -uo pipefail

# Read .env so the script works both from `make` and on its own.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "${REPO_ROOT}/.env"
    set +a
fi

CONTAINER="${KAFKA_CONTAINER:-search-metrics-kafka}"
BOOTSTRAP="${KAFKA_INTERNAL_BOOTSTRAP:-127.0.0.1:9092}"

if ! docker exec "$CONTAINER" true 2>/dev/null; then
    echo "Kafka container '$CONTAINER' is not running. Start the stack with: make dev" >&2
    exit 1
fi

kafka() {
    local script="$1"; shift
    docker exec "$CONTAINER" "/opt/kafka/bin/${script}" --bootstrap-server "$BOOTSTRAP" "$@"
}

echo
echo "Topics"
echo "------"
# Fields are label/value pairs, so read them by label rather than by position.
kafka kafka-topics.sh --describe 2>/dev/null | awk '
    /^Topic:/ && /PartitionCount:/ {
        name = partitions = replication = "?"
        for (i = 1; i <= NF; i++) {
            if ($i == "Topic:")             name = $(i + 1)
            if ($i == "PartitionCount:")    partitions = $(i + 1)
            if ($i == "ReplicationFactor:") replication = $(i + 1)
        }
        printf "  %-24s partitions=%-3s replication=%s\n", name, partitions, replication
    }'

echo
echo "Consumer groups"
echo "---------------"
groups=$(kafka kafka-consumer-groups.sh --list 2>/dev/null | grep -v '^$' || true)

if [[ -z "$groups" ]]; then
    echo "  (none yet — no consumer has connected)"
else
    for group in $groups; do
        echo "  $group"
        kafka kafka-consumer-groups.sh --describe --group "$group" 2>/dev/null \
            | awk 'NR>1 && NF>5 {printf "    %-24s partition=%-3s lag=%s\n", $2, $3, $6}'
    done
fi
echo
