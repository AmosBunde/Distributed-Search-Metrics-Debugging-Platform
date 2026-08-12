import type { ReactNode } from 'react'

import { ApiError } from '../api/client'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="skeleton" role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {hint ? <p>{hint}</p> : null}
    </div>
  )
}

/**
 * An error the reader can act on: an unreachable gateway and a 503 from
 * ClickHouse need different responses, so they are not collapsed into
 * "something went wrong".
 */
export function Failure({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const advice =
    error.status === 0
      ? 'The API gateway is unreachable. Is the stack running? Try: make dev'
      : error.status === 503
        ? 'The analytics store could not answer. Check ClickHouse: make health'
        : error.status === 404
          ? 'Nothing recorded for that identifier.'
          : 'The gateway rejected the request.'

  return (
    <div className="state error" role="alert">
      <h3>{advice}</h3>
      <p className="mono">
        {error.status ? `HTTP ${error.status} — ` : ''}
        {error.message}
      </p>
      {onRetry ? (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}

/** One state component so no view invents its own loading/empty/error handling. */
export function AsyncSection<T>({
  state,
  empty,
  children,
}: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }
  empty?: { title: string; hint?: ReactNode; when?: (data: T) => boolean }
  children: (data: T) => ReactNode
}) {
  if (state.loading) return <Loading />
  if (state.error) return <Failure error={state.error} onRetry={state.reload} />
  if (!state.data) return <Empty title="No data" />
  if (empty?.when?.(state.data)) return <Empty title={empty.title} hint={empty.hint} />
  return <>{children(state.data)}</>
}
