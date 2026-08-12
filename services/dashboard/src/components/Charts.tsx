import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { ErrorPoint, LatencyPoint, RelevancePoint } from '../api/client'
import { format } from './Stat'

/**
 * The API returns one row per (bucket, service). Charts want one row per bucket
 * with a column per service, so the pivot happens once here rather than in each
 * view.
 */
export function pivot<T extends { bucket: string; service: string }>(
  series: T[],
  value: (row: T) => number | null,
): { rows: Record<string, string | number | null>[]; services: string[] } {
  const byBucket = new Map<string, Record<string, string | number | null>>()
  const services = new Set<string>()

  for (const row of series) {
    services.add(row.service)
    const existing = byBucket.get(row.bucket) ?? { bucket: row.bucket }
    existing[row.service] = value(row)
    byBucket.set(row.bucket, existing)
  }

  return {
    rows: [...byBucket.values()].sort((a, b) => String(a.bucket).localeCompare(String(b.bucket))),
    services: [...services].sort(),
  }
}

// Distinct hues rather than a gradient: these are categories, not a scale.
const SERIES_COLOURS = ['#34d399', '#22d3ee', '#a78bfa', '#fbbf24', '#fb7185', '#60a5fa']

const AXIS = { stroke: '#64748b', fontSize: 11 }
const TOOLTIP_STYLE = {
  background: '#0b1220',
  border: '1px solid #334155',
  borderRadius: 8,
  fontSize: 12,
}

function timeLabel(bucket: string): string {
  return format.time(bucket)
}

interface TimeSeriesProps {
  rows: Record<string, string | number | null>[]
  keys: string[]
  formatValue: (value: number) => string
  height?: number
}

function TimeSeries({ rows, keys, formatValue, height = 240 }: TimeSeriesProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid stroke="#1e293b" vertical={false} />
        <XAxis dataKey="bucket" tickFormatter={timeLabel} {...AXIS} minTickGap={40} />
        <YAxis tickFormatter={(value: number) => formatValue(value)} {...AXIS} width={64} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={timeLabel}
          formatter={(value) => formatValue(Number(value))}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((key, index) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={SERIES_COLOURS[index % SERIES_COLOURS.length]}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

export function LatencyChart({
  series,
  percentile = 'p95',
}: {
  series: LatencyPoint[]
  percentile?: 'p50' | 'p95' | 'p99'
}) {
  const { rows, services } = pivot(series, (row) => row[percentile])
  return <TimeSeries rows={rows} keys={services} formatValue={(value) => format.ms(value)} />
}

export function ErrorRateChart({ series }: { series: ErrorPoint[] }) {
  const { rows, services } = pivot(series, (row) => row.error_rate ?? 0)
  return (
    <TimeSeries rows={rows} keys={services} formatValue={(value) => format.percent(value, 1)} />
  )
}

export function RelevanceChart({ series }: { series: RelevancePoint[] }) {
  const { rows, services } = pivot(series, (row) => row.avg_score)
  return <TimeSeries rows={rows} keys={services} formatValue={(value) => value.toFixed(2)} />
}

export function VolumeChart({ series }: { series: LatencyPoint[] }) {
  const { rows, services } = pivot(series, (row) => row.queries)
  return <TimeSeries rows={rows} keys={services} formatValue={(value) => format.count(value)} />
}
