import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, api, type Finding, type ReplayJob } from '../api/client'
import { AsyncSection, Failure } from '../components/States'
import { format } from '../components/Stat'
import { Waterfall } from '../components/Waterfall'
import { useQuery } from '../hooks/useQuery'

/** Confidence bands, so the border colour means the same thing every time. */
function band(confidence: number): 'high' | 'medium' | 'low' {
  if (confidence >= 0.8) return 'high'
  if (confidence >= 0.55) return 'medium'
  return 'low'
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className={`finding ${band(finding.confidence)}`}>
      <div className="finding-head">
        <strong>{finding.summary}</strong>
        <span className="badge neutral">{Math.round(finding.confidence * 100)}% confident</span>
      </div>
      <p className="finding-evidence">
        {finding.kind}
        {finding.span_id ? ` · span ${finding.span_id}` : ''}
        {'\n'}
        {Object.entries(finding.evidence)
          .map(([key, value]) => `${key}: ${String(value)}`)
          .join('\n')}
      </p>
    </article>
  )
}

function ReplayPanel({ queryId }: { queryId: string }) {
  const [job, setJob] = useState<ReplayJob | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [running, setRunning] = useState(false)

  const replay = async () => {
    setRunning(true)
    setError(null)
    try {
      setJob(await api.replay(queryId))
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(String(cause), 0))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card">
      <h2>Replay</h2>
      <p className="subtitle">
        Re-runs this query against its service and diffs the result. Explicit on
        purpose: it issues a real query.
      </p>

      <button type="button" className="primary" onClick={replay} disabled={running}>
        {running ? 'Replaying…' : 'Replay this query'}
      </button>

      {error ? (
        <div style={{ marginTop: 12 }}>
          <Failure error={error} />
        </div>
      ) : null}

      {job ? (
        <dl style={{ marginTop: 14, display: 'grid', gap: 6 }}>
          <div>
            <dt className="stat-label">Verdict</dt>
            <dd style={{ margin: 0 }}>
              {job.status === 'failed' ? (
                <span className="badge critical">{job.error ?? 'replay failed'}</span>
              ) : (
                <span className={`badge ${job.diff?.results_match ? 'ok' : 'warning'}`}>
                  {job.diff?.verdict ?? job.status}
                </span>
              )}
            </dd>
          </div>
          {job.diff ? (
            <>
              <div>
                <dt className="stat-label">Latency</dt>
                <dd style={{ margin: 0 }}>
                  {format.ms(job.original?.latency_ms)} → {format.ms(job.replay?.latency_ms)} (
                  {job.diff.latency_ratio.toFixed(2)}×)
                </dd>
              </div>
              <div>
                <dt className="stat-label">Results</dt>
                <dd style={{ margin: 0 }}>
                  {job.diff.results_match
                    ? 'identical document set'
                    : `${job.diff.added_documents.length} added, ${job.diff.removed_documents.length} removed`}
                </dd>
              </div>
            </>
          ) : null}
        </dl>
      ) : null}
    </div>
  )
}

export function DebugPage() {
  const { queryId } = useParams()
  const navigate = useNavigate()
  const [input, setInput] = useState(queryId ?? '')

  const bundle = useQuery(
    () => (queryId ? api.debugQuery(queryId) : Promise.resolve(null)),
    [queryId],
  )

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (input.trim()) navigate(`/debug/${encodeURIComponent(input.trim())}`)
  }

  return (
    <>
      <section className="card">
        <h2>Debug a query</h2>
        <p className="subtitle">Root cause analysis for one recorded search</p>
        <form onSubmit={submit} style={{ display: 'flex', gap: 8 }}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Query id"
            aria-label="Query id"
            style={{ flex: 1, maxWidth: 480 }}
          />
          <button type="submit" className="primary">
            Analyse
          </button>
        </form>
      </section>

      {queryId ? (
        <AsyncSection state={bundle}>
          {(data) =>
            data ? (
              <>
                <section className="card" style={{ marginTop: 'var(--gap)' }}>
                  <h2>{data.summary}</h2>
                  <p className="subtitle">
                    {data.slowest_service
                      ? `Most time spent in ${data.slowest_service.service} (${format.ms(
                          data.slowest_service.self_time_ms,
                        )} of self time)`
                      : 'No span data for this query'}
                  </p>

                  {data.findings.length ? (
                    data.findings.map((finding, index) => (
                      <FindingCard key={`${finding.kind}-${index}`} finding={finding} />
                    ))
                  ) : (
                    <div className="state">
                      <h3>Nothing stood out</h3>
                      <p>The trace looks unremarkable against each service&rsquo;s baseline.</p>
                    </div>
                  )}
                </section>

                <section className="panels">
                  <div className="card">
                    <h2>Trace</h2>
                    <p className="subtitle">
                      {data.trace.span_count} spans ·{' '}
                      {data.trace.trace_id ? (
                        <Link to={`/traces/${encodeURIComponent(data.trace.trace_id)}`}>
                          open in the trace explorer
                        </Link>
                      ) : (
                        'no trace recorded'
                      )}
                    </p>
                    {data.trace.span_count ? (
                      <Waterfall trace={data.trace} />
                    ) : (
                      <div className="state">
                        <h3>No spans for this query</h3>
                        <p>The services involved may not be exporting traces.</p>
                      </div>
                    )}
                  </div>

                  <ReplayPanel queryId={data.query_id} />
                </section>
              </>
            ) : null
          }
        </AsyncSection>
      ) : (
        <section className="state" style={{ marginTop: 'var(--gap)' }}>
          <h3>Enter a query id</h3>
          <p>Or pick one from the slowest queries on the overview.</p>
        </section>
      )}
    </>
  )
}
