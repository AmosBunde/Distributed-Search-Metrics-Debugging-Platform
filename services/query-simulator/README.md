# Query Simulator

Generates realistic search traffic so the platform has something to measure.

```bash
make simulate QPS=500                                   # steady traffic
make simulate SCENARIO=error_spike QPS=1000             # a dependency starts failing
make simulate SCENARIO=slow_queries DURATION=300        # latency degrades
make simulate SCENARIO=anomaly_spike                    # exercises the detector
make scenarios                                          # list them with their phases
```

It never starts with `make dev` — it lives behind a compose profile and runs
only when asked.

## Scenarios

A scenario is a schedule of **phases**, and that structure is the point: an
anomaly detector can only be exercised by traffic that is normal first and
abnormal afterwards.

| Scenario | Shape |
|---|---|
| `baseline` | Steady, healthy traffic. What normal looks like |
| `error_spike` | 2 min warm-up → 3 min at 35% errors on two services → recovery |
| `slow_queries` | 2 min warm-up → 5 min at 6× latency with the cache cold → recovery |
| `anomaly_spike` | 7 min baseline → 2 min at 12× latency and 2.5× volume → aftermath |
| `traffic_drop` | 7 min baseline → 3 min at 5% of normal volume → recovery |

The long baselines are deliberate. The detector needs at least five closed
windows of history before it will report anything (see the metrics engine), so a
scenario that spikes immediately would produce nothing and look like a bug.

## What makes the traffic realistic

**Latency is log-normal.** Real search latency has a long right tail, which is
precisely why anyone watches p99. Uniform noise would make the percentile panels
meaningless and flatter the anomaly detector.

**A failed query still carries a latency.** Errors arriving as zero-latency
events would quietly drag every percentile down. Timeouts are reported as slow,
because that is what a timeout is.

**Cache hits are faster than misses**, so `cache_hit_rate` moves latency the way
it does in production.

**Per-service latency profiles differ** — `suggest-service` answers in ~25 ms,
`index-service` in ~200 ms — so the dashboard's service comparison shows
something other than four identical lines.

Everything is seeded: the same `--seed` produces byte-identical traffic, so a
test can assert on it and an investigation can be reproduced.

## Direct use

```bash
python -m simulator --qps 500 --scenario error_spike --duration 120 \
  --collector http://localhost:8001 --seed 42
docker compose run --rm query-simulator --list
```

The runner paces against the wall clock rather than sleeping a fixed amount per
batch, and reports the rate it actually achieved:

```
sent 9000 events in 30.0s (300 qps, -0% vs target) — accepted 9000, rejected 0, failed requests 0
```

If it cannot keep up it says so rather than quietly delivering half the traffic
you asked for. That is how a 12× throughput bug in the ingest path was found:
the collector was publishing to Kafka one message at a time, so 300 events per
batch meant 300 serialized round trips, and the simulator reported `25 qps,
-92% vs target`.

## Tests

```bash
make test-unit
```

Generation is seeded, so the tests assert on real generated traffic: that the
median latency lands where it was asked to, that the tail is genuinely long,
that a degraded phase is measurably slower, that failures still carry latencies,
and that every generated event passes the platform's own validation — traffic
the collector would reject is not a useful simulator.
