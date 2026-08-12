# 0004. Use OpenTelemetry as the only tracing standard

- **Status:** Accepted
- **Date:** 2026-08-12

## Context

The platform's debugging half is only as good as the traces it receives. A
useful root cause analysis needs a complete span tree for a query: which service
was slow, which call failed, where retries happened. That requires one trace
context propagated across every hop — the instrumented search services, the
collector, Kafka, the engine, and the debug service itself.

We also do not want to own an instrumentation format that adopters would have to
adopt in their own services just to use this platform.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/debug-request-dark.svg">
  <img alt="Sequence: operator opens a slow query, the debug service assembles a trace and ranks findings" src="../diagrams/debug-request-light.svg">
</picture>

## Decision

Every service is instrumented with OpenTelemetry and exports OTLP. W3C Trace
Context headers propagate across HTTP, and trace context travels through Kafka in
message headers. Jaeger is the local trace backend; any OTLP-compatible backend
can replace it in production by changing one endpoint.

## Consequences

### What this makes easy

- Adopters instrument once, with a vendor-neutral SDK they very likely already
  use, and their services appear in the traces without bespoke work.
- Logs carry trace and span ids, so a log line links to the trace that produced it.
- Swapping the trace backend is configuration, not code.

### What this makes hard

- Trace context has to be propagated manually across the Kafka boundary — it is
  not automatic the way an HTTP client instrumentation is. The shared library
  does this in one place so services cannot get it wrong individually.
- Full sampling is affordable in development and expensive in production, so
  sampling rate becomes an operational decision with a real trade-off: sample
  too aggressively and the one slow query an operator cares about is missing.

### What we now have to live with

An OTLP collection endpoint in every environment, and the storage that backs it.
Trace retention is typically much shorter than metrics retention.

## Alternatives considered

### Jaeger client libraries directly

Rejected. Jaeger's own clients are deprecated in favour of OpenTelemetry, and
they would tie instrumentation to one backend.

### Zipkin

Rejected. Smaller ecosystem and fewer auto-instrumentation libraries; no reason
to prefer it now that OTLP is the industry default.

### Structured logs with a correlation id, no tracing

Rejected. It answers "what happened" but not "how long did each hop take", and
span timings are exactly what root cause analysis ranks on.

## Revisit when

OpenTelemetry's Python SDK stops being viable for a component, or a specific
backend's native format offers something OTLP genuinely cannot express.
