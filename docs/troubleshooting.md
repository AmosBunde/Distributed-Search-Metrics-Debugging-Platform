# Troubleshooting the local stack

Problems that actually happened while building this, and what fixed them.

## `make dev` fails with "port is already allocated"

```
Error response from daemon: driver failed programming external connectivity ...
Bind for 0.0.0.0:5432 failed: port is already allocated
```

Another process on your machine — often a different project's Postgres, Redis or
Kafka UI — already holds that host port. Every port is configurable, so change
yours rather than stopping theirs:

```bash
# find the culprit
ss -tlnp | grep :5432
docker ps --format '{{.Names}}\t{{.Ports}}'

# then edit .env
POSTGRES_PORT=5433
CLICKHOUSE_PORT=8124
REDIS_PORT=6380
KAFKA_UI_PORT=8081
```

Only the *host* side changes. Services talk to each other over the compose
network on their standard ports, so nothing else needs adjusting, and
`make health` and `make check-metrics` read the same `.env`.

## ClickHouse stays unhealthy but its logs look fine

Check what the healthcheck actually reported:

```bash
docker inspect search-metrics-clickhouse --format '{{json .State.Health}}' | python3 -m json.tool
```

If it says `connection refused` while the server logs say it is listening, the
cause is usually IPv6: `localhost` resolves to `::1` inside the container, the
server bound `0.0.0.0` only, and nothing is listening on the IPv6 loopback. Every
healthcheck in this repo uses `127.0.0.1` for exactly this reason — a unit test
enforces it.

## ClickHouse logs "Address already in use" on its own ports

The entrypoint runs a temporary server to apply `init.sql`, then starts the real
one. If a previous run left a partially initialised data volume, the two can
overlap. Start clean:

```bash
make clean-volumes && make dev
```

## The collector restarts with "Compression library for lz4 not found"

`aiokafka` delegates lz4, snappy and zstd to **cramjam** — the standalone `lz4`
package is not what its `has_lz4()` checks. The shared library depends on
`aiokafka[lz4]` and `cramjam` for this reason. If you see it, rebuild:

```bash
docker compose build --no-cache telemetry-collector
```

## `make health` says a service answered "but not this service"

Something else is listening on that port. Health checks assert that the response
identifies the expected service, so an unrelated API on port 8000 is reported
rather than counted as ours. Change the port in `.env`, or stop the other
process.

## Kafka topics are missing

`kafka-init` creates them once Kafka is healthy, then exits — an `Exited (0)`
status is success, not a failure. Check what it did:

```bash
docker compose logs kafka-init
make check-kafka
```

Auto-creation is deliberately disabled, so a typo in a topic name fails loudly
instead of silently creating an empty topic nobody consumes.

## Nothing in ClickHouse after generating traffic

Follow the pipeline in order and stop at the first surprise:

```bash
make check-kafka      # are events reaching the broker? is a consumer lagging?
make check-metrics    # are rows landing in ClickHouse?
docker compose logs metrics-engine --tail=50
```

If Kafka has messages but ClickHouse has no rows, the metrics engine is the
problem. If Kafka has nothing, the collector is — check its logs and confirm
your events are not being rejected with `422`.

## Starting over

```bash
make clean-volumes    # stop everything and delete all data
make dev
```
