# Tests

Three suites, split by what they need rather than by what they cover.

| Suite | Needs | Runs in | Command |
|---|---|---|---|
| `unit/` | Nothing | ~15 s | `make test-unit` |
| `integration/` | The stack (`make dev`) | ~45 s | `make test-integration` |
| `e2e/` | The stack (`make dev`) | ~30 s | `make test-e2e` |

```bash
make test-unit          # 385 tests, no infrastructure
make dev                # then, with the stack up:
make test-integration   # 20 tests, each hop of the pipeline
make test-e2e           # 10 tests, whole workflows through the gateway
make coverage           # unit coverage, gated at 80%
```

## What each suite is for

**Unit** — every service's logic with its dependencies faked. This is where the
arithmetic lives: percentiles, z-scores, window boundaries, trace assembly,
replay diffing, rate limiting. Fast enough to run on every save.

**Integration** — the joins the unit tests deliberately fake. A real broker, real
SQL against the real schema, real serialisation across a network. These catch
the class of bug that unit tests structurally cannot: an `asyncpg` bind that
needs a `datetime` rather than a string, a ClickHouse alias that shadows the
column it filters on, a compression codec that is not actually installed.

**End to end** — whole operator workflows through the public API only, never
reaching into ClickHouse or Kafka. If a workflow passes here it works for a
user; if it fails here a user is broken, whatever the internals say. The
sequence that matters most is covered directly: traffic arrives → a slow query
appears in the slowest list → its debug bundle names the failing span → its
trace opens → a replay returns a verdict.

## Conventions

**Skip, do not fail, when the stack is down.** `pytest tests/` on a laptop with
nothing running is quiet, and says which service was unreachable.

**Poll, never sleep.** The pipeline is asynchronous by design — ingest, Kafka, a
window closing, a batched insert — so every wait is `eventually(...)` with a
timeout and a description. A fixed sleep is either flaky on a loaded machine or
slow for everyone, and it hides how long the pipeline actually takes.

**Unique identifiers per test.** Every test generates its own query id or
service name, so the suites can run repeatedly against a stack that already has
data in it without interfering with each other.

**Configuration comes from `.env`.** The suites read the same ports the stack
was started with, so they work when a port has been changed to avoid a conflict.

## Windows and timing

Several tests timestamp their events into a *past* window on purpose. The
metrics engine emits a window once its watermark has moved past the end, so
events landing in the current window are — correctly — still open, and would
never appear inside a test's lifetime. Writing to a past window and then sending
one current event is what closes it.
