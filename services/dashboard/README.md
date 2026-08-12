# Dashboard

The operator-facing UI: React 18, TypeScript, Vite, Recharts.

```bash
make dev                       # served at http://localhost:3000
npm run dev                    # local dev server with hot reload
npm run test                   # component tests
npm run build                  # production bundle
```

## Views

| Route | Purpose |
|---|---|
| `/overview` | Summary cards, per-service table, latency/error/relevance/volume charts, slowest queries |
| `/anomalies` | The anomaly feed, with the evidence behind each detection |
| `/traces/:traceId` | Trace explorer with a waterfall |
| `/debug/:queryId` | Ranked root cause findings, the trace, and replay |

The views are connected: a slow query on the overview links to its debug page,
which links to its trace.

## Decisions worth knowing

**One origin.** nginx serves the bundle and proxies `/api` to the gateway, so
the browser never makes a cross-origin request. No CORS, and no API URL baked
into the bundle at build time — the same image runs in every environment.

**A refresh never blanks the screen.** Auto-refresh keeps the previous data
visible while the next request is in flight. An operator watching a chart should
not see it flash back to a skeleton every thirty seconds.

**Absent is not zero.** A missing value renders as `—`, and a service with no
data in a bucket leaves a gap rather than a line to the floor — a zero would
read as an outage.

**Errors say what to do.** An unreachable gateway, a 503 from ClickHouse and a
404 are three different problems, and each gets its own message: *"The API
gateway is unreachable. Is the stack running? Try: make dev"* beats *"something
went wrong"*.

**Empty states point somewhere.** No traffic suggests `make simulate QPS=500`;
no anomalies suggests `make simulate SCENARIO=anomaly_spike`.

**The bucket size follows the range.** Minute buckets over seven days would be
ten thousand points nobody can read, so the interval steps up with the window.

**Thresholds live in one place.** `errorTone` and `latencyTone` decide what
counts as green, amber and red, so a number never means one thing in a card and
another in a table.

## Accessibility

Semantic landmarks, labelled controls (`Time range`, `Service filter`, `Auto
refresh interval`), visible focus rings, `role="alert"` on failures and
`role="status"` on loading, and `prefers-reduced-motion` honoured for the
skeleton shimmer. Charts are paired with tables so the data is never
colour-only.

## Tests

```bash
npm run test
```

24 component tests covering the parts where being wrong would mislead an
operator: number formatting (absent versus zero), threshold colours, the chart
pivot (including that a missing series stays undefined rather than becoming a
zero), waterfall geometry (a child that starts halfway through its parent is
drawn halfway along), the loading/empty/error states, and that changing the time
range actually refetches with the new window.
