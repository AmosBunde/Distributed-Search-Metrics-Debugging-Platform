import { useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../api/client'
import { AsyncSection } from '../components/States'
import { format } from '../components/Stat'
import { Waterfall } from '../components/Waterfall'
import { useQuery } from '../hooks/useQuery'

export function TracesPage() {
  const { traceId } = useParams()
  const navigate = useNavigate()
  const [input, setInput] = useState(traceId ?? '')

  const trace = useQuery(
    () => (traceId ? api.trace(traceId) : Promise.resolve(null)),
    [traceId],
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (input.trim()) navigate(`/traces/${encodeURIComponent(input.trim())}`)
  }

  return (
    <>
      <section className="card">
        <h2>Trace explorer</h2>
        <p className="subtitle">Every span of one distributed request, in order</p>
        <form onSubmit={submit} style={{ display: 'flex', gap: 8 }}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Trace id"
            aria-label="Trace id"
            style={{ flex: 1, maxWidth: 480 }}
          />
          <button type="submit" className="primary">
            Open trace
          </button>
        </form>
      </section>

      {traceId ? (
        <section className="card" style={{ marginTop: 'var(--gap)' }}>
          <AsyncSection state={trace}>
            {(data) =>
              data ? (
                <>
                  <h2 className="mono">{data.trace_id}</h2>
                  <p className="subtitle">
                    {data.span_count} spans across {data.services.length} service
                    {data.services.length === 1 ? '' : 's'} · {format.ms(data.total_duration_ms)}
                    {data.orphan_count ? ` · ⚠ ${data.orphan_count} span(s) missing a parent` : ''}
                  </p>
                  <Waterfall trace={data} />
                </>
              ) : null
            }
          </AsyncSection>
        </section>
      ) : (
        <section className="state" style={{ marginTop: 'var(--gap)' }}>
          <h3>Enter a trace id</h3>
          <p>Or reach one from a slow query on the overview.</p>
        </section>
      )}
    </>
  )
}
