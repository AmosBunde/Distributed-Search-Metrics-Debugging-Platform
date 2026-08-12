# Architecture Decision Records

An ADR records a decision that constrains future work — a datastore, a protocol,
a deployment model — together with the reasoning and the consequences we accepted.
They are written once and then left alone: if a decision changes, add a new ADR
that supersedes the old one rather than editing history.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-kafka-event-backbone.md) | Kafka as the event backbone | Accepted |
| [0002](0002-clickhouse-for-analytics.md) | ClickHouse for search metrics storage | Accepted |
| [0003](0003-python-consumer-instead-of-flink.md) | A Python consumer, not PyFlink, for v1 | Accepted |
| [0004](0004-opentelemetry-tracing.md) | OpenTelemetry as the only tracing standard | Accepted |
| [0005](0005-multi-cloud-terraform-layout.md) | One module set, three cloud environments | Accepted |
| [0006](0006-archify-diagrams-as-svg.md) | Generated Archify diagrams, committed as SVG | Accepted |

## Adding one

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, taking the next number.
2. Fill in context, decision, consequences and the alternatives you rejected.
   The alternatives section is the valuable part — it is what stops the same
   discussion happening again in six months.
3. Add a row to the index above.
4. If the decision is easier to see than to read, generate a diagram for it:
   add a spec under `docs/diagrams/src/` and run `make diagrams`.

## Status values

- **Proposed** — under discussion, not yet acted on.
- **Accepted** — in effect; the code reflects it.
- **Superseded by NNNN** — replaced. Keep the original text intact.
