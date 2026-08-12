import type { SpanNode, Trace } from '../api/client'
import { format } from './Stat'

interface Row {
  span: SpanNode
  offsetMs: number
}

/**
 * Flatten the span tree into rows positioned against the trace's own start, so
 * the bars line up the way an operator reads them: left is earlier.
 */
export function layout(trace: Trace): { rows: Row[]; startMs: number; totalMs: number } {
  const rows: Row[] = []

  const walk = (node: SpanNode) => {
    rows.push({ span: node, offsetMs: new Date(node.start_time).getTime() })
    node.children.forEach(walk)
  }
  trace.roots.forEach(walk)

  const startMs = rows.length ? Math.min(...rows.map((row) => row.offsetMs)) : 0
  const endMs = rows.length
    ? Math.max(...rows.map((row) => row.offsetMs + row.span.duration_ms))
    : 0

  return { rows, startMs, totalMs: Math.max(1, endMs - startMs) }
}

export function Waterfall({ trace }: { trace: Trace }) {
  const { rows, startMs, totalMs } = layout(trace)
  const criticalPath = new Set(
    (trace.critical_path ?? []).map((step) => `${step.service}:${step.operation}`),
  )

  return (
    <div className="waterfall" role="table" aria-label="Trace waterfall">
      {rows.map(({ span, offsetMs }) => {
        const left = ((offsetMs - startMs) / totalMs) * 100
        const width = Math.max(0.5, (span.duration_ms / totalMs) * 100)
        const onCriticalPath = criticalPath.has(`${span.service}:${span.operation}`)
        const tone = span.status !== 'ok' ? 'error' : onCriticalPath ? 'critical-path' : ''

        return (
          <div className="waterfall-row" role="row" key={span.span_id}>
            <span
              className="waterfall-label"
              role="cell"
              style={{ paddingLeft: `${span.depth * 14}px` }}
              title={`${span.service} · ${span.operation}`}
            >
              {span.orphaned ? '⚠ ' : ''}
              <strong>{span.service}</strong>{' '}
              <span style={{ color: 'var(--text-dim)' }}>{span.operation}</span>
            </span>

            <span className="waterfall-track" role="cell">
              <span
                className={`waterfall-bar ${tone}`}
                style={{ left: `${left}%`, width: `${width}%` }}
                title={`${span.duration_ms.toFixed(1)} ms (${span.self_time_ms.toFixed(1)} ms self)`}
              />
            </span>

            <span className="waterfall-duration" role="cell">
              {format.ms(span.duration_ms)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
