/**
 * Typed client for the API gateway.
 *
 * Every response shape here mirrors what the gateway actually returns, so a
 * change on either side surfaces as a type error rather than as `undefined`
 * appearing in a chart.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export interface Window {
  start: string
  end: string
}

export interface LatencyPoint {
  bucket: string
  service: string
  p50: number
  p95: number
  p99: number
  avg: number
  max: number
  queries: number
}

export interface ErrorPoint {
  bucket: string
  service: string
  queries: number
  errors: number
  error_rate: number | null
}

export interface RelevancePoint {
  bucket: string
  service: string
  avg_score: number | null
  p10_score: number | null
  queries: number
}

export interface SummaryTotals {
  queries: number | null
  errors: number | null
  error_rate: number | null
  p50: number | null
  p95: number | null
  p99: number | null
  relevance: number | null
  cache_hit_rate: number | null
  services: number | null
  open_anomalies: number | null
}

export interface ServiceRow {
  service: string
  queries: number
  p95: number | null
  error_rate: number | null
}

export interface Anomaly {
  anomaly_id: string
  service: string
  metric: string
  window_start: string
  window_end: string
  observed: number
  baseline_mean: number
  baseline_stddev: number
  z_score: number
  severity: 'info' | 'warning' | 'critical'
  sample_count: number
  detected_at: string
}

export interface SlowQuery {
  query_id: string
  trace_id: string
  service: string
  query: string
  latency_ms: number
  status: string
  result_count: number
  relevance_score: number | null
  timestamp: string
}

export interface SpanNode {
  span_id: string
  parent_span_id: string
  service: string
  operation: string
  start_time: string
  duration_ms: number
  self_time_ms: number
  status: string
  depth: number
  orphaned: boolean
  attributes: Record<string, string>
  children: SpanNode[]
}

export interface Trace {
  trace_id: string
  span_count: number
  orphan_count: number
  total_duration_ms: number
  services: string[]
  roots: SpanNode[]
  critical_path?: { service: string; operation: string; duration_ms: number }[]
}

export interface Finding {
  kind: string
  summary: string
  confidence: number
  service: string
  span_id: string
  evidence: Record<string, unknown>
}

export interface DebugBundle {
  query_id: string
  event: Record<string, unknown> | null
  summary: string
  findings: Finding[]
  slowest_service: { service: string; self_time_ms: number } | null
  trace: Trace
  baselines: Record<string, number>
}

export interface ReplayJob {
  id: string
  query_id: string
  target_service: string
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  error: string | null
  original: { latency_ms: number; result_count: number; status: string } | null
  replay: { latency_ms: number; result_count: number; status: string } | null
  diff: {
    latency_delta_ms: number
    latency_ratio: number
    results_match: boolean
    verdict: string
    added_documents: string[]
    removed_documents: string[]
  } | null
}

/** A failed request that still carries the status, so callers can react to it. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { 'content-type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    // A network failure and a 500 are different problems for the reader: one
    // means the gateway is unreachable, the other that it answered badly.
    throw new ApiError(`Could not reach the API: ${(cause as Error).message}`, 0)
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new ApiError(detail?.slice(0, 300) || response.statusText, response.status)
  }

  return (await response.json()) as T
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  }
  const rendered = search.toString()
  return rendered ? `?${rendered}` : ''
}

export interface MetricsFilters {
  minutes: number
  service?: string
  interval: string
}

export const api = {
  summary: (minutes: number) =>
    request<{ window: Window; totals: SummaryTotals; services: ServiceRow[] }>(
      `/api/v1/metrics/summary${query({ minutes })}`,
    ),

  latency: ({ minutes, service, interval }: MetricsFilters) =>
    request<{ window: Window; series: LatencyPoint[]; note: string }>(
      `/api/v1/metrics/latency${query({ minutes, service, interval })}`,
    ),

  errors: ({ minutes, service, interval }: MetricsFilters) =>
    request<{ window: Window; series: ErrorPoint[] }>(
      `/api/v1/metrics/errors${query({ minutes, service, interval })}`,
    ),

  relevance: ({ minutes, service, interval }: MetricsFilters) =>
    request<{ window: Window; series: RelevancePoint[] }>(
      `/api/v1/metrics/relevance${query({ minutes, service, interval })}`,
    ),

  anomalies: (params: { minutes: number; service?: string; severity?: string; limit?: number }) =>
    request<{ count: number; anomalies: Anomaly[] }>(`/api/v1/anomalies${query(params)}`),

  slowestQueries: (params: { minutes: number; service?: string; limit?: number }) =>
    request<{ count: number; queries: SlowQuery[] }>(`/api/v1/queries/slowest${query(params)}`),

  trace: (traceId: string) => request<Trace>(`/api/v1/traces/${encodeURIComponent(traceId)}`),

  debugQuery: (queryId: string) =>
    request<DebugBundle>(`/api/v1/debug/query/${encodeURIComponent(queryId)}`),

  replay: (queryId: string, targetService?: string) =>
    request<ReplayJob>('/api/v1/debug/replay', {
      method: 'POST',
      body: JSON.stringify({ query_id: queryId, target_service: targetService }),
    }),
}
