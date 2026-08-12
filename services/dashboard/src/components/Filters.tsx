import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'

export const RANGES = [
  { label: 'Last 15 min', minutes: 15, interval: '1m' },
  { label: 'Last hour', minutes: 60, interval: '1m' },
  { label: 'Last 6 hours', minutes: 360, interval: '5m' },
  { label: 'Last 24 hours', minutes: 1440, interval: '15m' },
  { label: 'Last 7 days', minutes: 10080, interval: '1h' },
] as const

export const REFRESH_OPTIONS = [
  { label: 'Off', ms: 0 },
  { label: '10s', ms: 10_000 },
  { label: '30s', ms: 30_000 },
  { label: '1m', ms: 60_000 },
] as const

export interface FiltersValue {
  minutes: number
  interval: string
  service: string
  refreshMs: number
  setMinutes: (minutes: number) => void
  setService: (service: string) => void
  setRefreshMs: (ms: number) => void
  knownServices: string[]
  setKnownServices: (services: string[]) => void
}

const FiltersContext = createContext<FiltersValue | null>(null)

export function FiltersProvider({ children }: { children: ReactNode }) {
  const [minutes, setMinutes] = useState<number>(60)
  const [service, setService] = useState<string>('')
  const [refreshMs, setRefreshMs] = useState<number>(30_000)
  const [knownServices, setKnownServices] = useState<string[]>([])

  // The bucket size follows the range: minute buckets over seven days would be
  // ten thousand points nobody can read.
  const interval = useMemo(
    () => RANGES.find((range) => range.minutes === minutes)?.interval ?? '1m',
    [minutes],
  )

  const value = useMemo(
    () => ({
      minutes,
      interval,
      service,
      refreshMs,
      setMinutes,
      setService,
      setRefreshMs,
      knownServices,
      setKnownServices,
    }),
    [minutes, interval, service, refreshMs, knownServices],
  )

  return <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>
}

export function useFilters(): FiltersValue {
  const value = useContext(FiltersContext)
  if (!value) throw new Error('useFilters must be used inside a FiltersProvider')
  return value
}

export function FilterBar() {
  const { minutes, service, refreshMs, setMinutes, setService, setRefreshMs, knownServices } =
    useFilters()

  return (
    <>
      <label className="control">
        <span>Range</span>
        <select
          value={minutes}
          onChange={(event) => setMinutes(Number(event.target.value))}
          aria-label="Time range"
        >
          {RANGES.map((range) => (
            <option key={range.minutes} value={range.minutes}>
              {range.label}
            </option>
          ))}
        </select>
      </label>

      <label className="control">
        <span>Service</span>
        <select
          value={service}
          onChange={(event) => setService(event.target.value)}
          aria-label="Service filter"
        >
          <option value="">All services</option>
          {knownServices.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>

      <label className="control">
        <span>Refresh</span>
        <select
          value={refreshMs}
          onChange={(event) => setRefreshMs(Number(event.target.value))}
          aria-label="Auto refresh interval"
        >
          {REFRESH_OPTIONS.map((option) => (
            <option key={option.ms} value={option.ms}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    </>
  )
}
