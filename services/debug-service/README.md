# Debug Service

Answers "why was *this* query slow?" — the counterpart to the metrics engine,
which answers "how slow are queries in general?".

## API

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/v1/traces/{trace_id}` | The assembled span tree plus its critical path |
| `GET` | `/api/v1/debug/query/{query_id}` | Ranked root cause findings, the trace, and service baselines |
| `POST` | `/api/v1/debug/replay` | Re-runs a recorded query and diffs the result |
| `GET` | `/api/v1/debug/replay/{job_id}` | A previous replay job |

```bash
curl localhost:8003/api/v1/debug/query/q-1 | jq '.summary, .findings[0]'
```

```json
"index-service failed in fetch (error)"
{
  "kind": "error_span",
  "summary": "index-service failed in fetch (error)",
  "confidence": 0.95,
  "service": "index-service",
  "span_id": "s3",
  "evidence": { "status": "error", "duration_ms": 1200, "error": "shard timeout" }
}
```

## Trace assembly

Spans arrive out of order, from services that never coordinated, and the set is
often incomplete.

- **Out-of-order spans still nest** — the tree is built by id, not by arrival.
- **An orphan is kept and flagged, never dropped.** A span whose parent never
  arrived becomes an extra root marked `orphaned`, because the missing parent is
  usually the interesting part of the mystery.
- **A cycle is rejected** with a 422 rather than hanging the renderer.
- **Self time is what matters.** A span's duration minus its children's is what
  points at the culprit; a 10-second parent whose child took 9.9 seconds is not
  slow, its child is.

## Root cause findings

Every finding carries the evidence that produced it and a confidence score, so
an operator can disagree by looking at the same span the analyser looked at.

| Kind | Fires when | Confidence |
|---|---|---|
| `error_span` | A span reports a non-ok status | 0.95 — a fact, not an inference |
| `baseline_breach` | A span exceeded 2× its service's recent p95 | 0.55–0.95 by ratio |
| `slow_span` | A span owns ≥35% of the trace *and* ≥100 ms | 0.5–0.9 by share |
| `retry_storm` | One operation called ≥3 times under one parent | 0.4–0.85 by count |
| `missing_spans` | Part of the trace never arrived | 0.6 |
| `fan_out` | One span has ≥8 children | 0.45 |
| `cache_miss` | A span recorded `cache.hit=false` | 0.5 — usually contributing, rarely causal |

Findings are ranked by **kind first, confidence second**: a confident latency
observation must never outrank an outright failure.

Two rules keep it from becoming noise. A span is only "slow" if it is slow
against *its own service's baseline* — 900 ms in a service whose p95 is 850 ms
is not a finding. And a span must own at least 100 ms of real time, so a
trivially short trace does not produce a "slow span" just because something has
to dominate it.

## Replay

Replay answers what a trace cannot: does this still happen?

- Always explicit — analysis never triggers a replay on its own, because it
  re-issues a real query against a real service.
- Results are compared **by document set, not by rank**, so a score shuffle is
  not reported as a different result.
- Verdicts: `matches the original run`, `slower than the original run`,
  `different results`, `still failing`, `no longer reproducible`.
- A broken target is recorded on the job, not raised: "the target refused the
  connection" is an answer to the operator's question.

Jobs persist in PostgreSQL. If Postgres is unavailable the service degrades to
in-memory jobs and says so in `/health` rather than failing the request.

## Tests

```bash
make test-unit
```

Trace assembly gets the hostile inputs — out-of-order spans, orphans, cycles,
self-references, empty sets. The heuristics are tested for both firing *and* not
firing, since a debugging tool that always reports something is one people stop
reading.
