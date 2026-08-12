import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'

export interface QueryState<T> {
  data: T | null
  error: ApiError | null
  /** True only for the first load, so a refresh does not blank the screen. */
  loading: boolean
  refreshing: boolean
  reload: () => void
}

/**
 * Fetch on mount, on dependency change, and on an optional interval.
 *
 * Auto-refresh keeps the previous data on screen while the next request is in
 * flight: an operator watching a chart should never see it flash back to a
 * skeleton every few seconds.
 */
export function useQuery<T>(
  load: () => Promise<T>,
  dependencies: unknown[],
  refreshMs = 0,
): QueryState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [nonce, setNonce] = useState(0)

  const loadRef = useRef(load)
  loadRef.current = load

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    let active = true
    const isFirst = data === null

    if (!isFirst) setRefreshing(true)

    loadRef
      .current()
      .then((result) => {
        if (!active) return
        setData(result)
        setError(null)
      })
      .catch((cause) => {
        if (!active) return
        setError(
          cause instanceof ApiError ? cause : new ApiError(String(cause), 0),
        )
      })
      .finally(() => {
        if (!active) return
        setLoading(false)
        setRefreshing(false)
      })

    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, nonce])

  useEffect(() => {
    if (!refreshMs) return
    const timer = setInterval(reload, refreshMs)
    return () => clearInterval(timer)
  }, [refreshMs, reload])

  return { data, error, loading, refreshing, reload }
}
