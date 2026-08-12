#!/usr/bin/env sh
# Create the platform's topics. Runs as a one-shot container after Kafka is
# healthy, and is safe to run repeatedly — `--if-not-exists` makes it a no-op
# once the topics are there.
set -eu

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
PARTITIONS="${KAFKA_TOPIC_PARTITIONS:-6}"
RETENTION_MS="${KAFKA_TOPIC_RETENTION_MS:-604800000}"
REPLICATION="${KAFKA_TOPIC_REPLICATION:-1}"

# Partition count caps consumer parallelism (ADR-0001), so it is deliberately
# larger than the number of engine replicas we expect to run locally.
for topic in \
    "${KAFKA_TOPIC_EVENTS:-search.events}" \
    "${KAFKA_TOPIC_RESULTS:-search.results}" \
    "${KAFKA_TOPIC_ERRORS:-search.errors}" \
    "${KAFKA_TOPIC_ANOMALIES:-search.anomalies}"
do
    echo "creating topic ${topic} (${PARTITIONS} partitions, retention ${RETENTION_MS}ms)"
    /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server "${BOOTSTRAP}" \
        --create --if-not-exists \
        --topic "${topic}" \
        --partitions "${PARTITIONS}" \
        --replication-factor "${REPLICATION}" \
        --config "retention.ms=${RETENTION_MS}" \
        --config compression.type=lz4
done

echo
echo "topics now present:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "${BOOTSTRAP}" --list
