export type StatTone = 'neutral' | 'good' | 'warn' | 'bad'

export function Stat({
  label,
  value,
  note,
  tone = 'neutral',
}: {
  label: string
  value: string
  note?: string
  tone?: StatTone
}) {
  return (
    <div className="card stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone === 'neutral' ? '' : tone}`}>{value}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </div>
  )
}

/** Formatting helpers, kept here so every view renders numbers the same way. */
export const format = {
  count(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—'
    return new Intl.NumberFormat().format(value)
  },
  ms(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—'
    if (value >= 1000) return `${(value / 1000).toFixed(2)} s`
    return `${value.toFixed(value < 10 ? 1 : 0)} ms`
  },
  percent(value: number | null | undefined, digits = 2): string {
    if (value === null || value === undefined) return '—'
    return `${(value * 100).toFixed(digits)}%`
  },
  score(value: number | null | undefined): string {
    if (value === null || value === undefined) return '—'
    return value.toFixed(3)
  },
  time(value: string | null | undefined): string {
    if (!value) return '—'
    const parsed = new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z')
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString()
  },
}

/** Thresholds live in one place so the colours mean the same thing everywhere. */
export function errorTone(rate: number | null | undefined): StatTone {
  if (rate === null || rate === undefined) return 'neutral'
  if (rate >= 0.05) return 'bad'
  if (rate >= 0.01) return 'warn'
  return 'good'
}

export function latencyTone(p99: number | null | undefined): StatTone {
  if (p99 === null || p99 === undefined) return 'neutral'
  if (p99 >= 2000) return 'bad'
  if (p99 >= 1000) return 'warn'
  return 'good'
}
