import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { ErrorRateChart, LatencyChart, RelevanceChart, VolumeChart } from '../components/Charts'
import { useFilters } from '../components/Filters'
import { AsyncSection } from '../components/States'
import { Stat, errorTone, format, latencyTone } from '../components/Stat'
import { useQuery } from '../hooks/useQuery'

export function OverviewPage() {
  const { minutes, interval, service, refreshMs, setKnownServices } = useFilters()
  const filters = { minutes, interval, service: service || undefined }

  const summary = useQuery(() => api.summary(minutes), [minutes], refreshMs)
  const latency = useQuery(() => api.latency(filters), [minutes, interval, service], refreshMs)
  const errors = useQuery(() => api.errors(filters), [minutes, interval, service], refreshMs)
  const relevance = useQuery(() => api.relevance(filters), [minutes, interval, service], refreshMs)
  const slowest = useQuery(
    () => api.slowestQueries({ minutes, service: service || undefined, limit: 10 }),
    [minutes, service],
    refreshMs,
  )

  // The service filter's options come from what actually reported traffic,
  // rather than from a hardcoded list that quietly goes stale.
  useEffect(() => {
    if (summary.data) setKnownServices(summary.data.services.map((row) => row.service))
  }, [summary.data, setKnownServices])

  return (
    <>
      <AsyncSection
        state={summary}
        empty={{
          title: 'No traffic in this window',
          hint: 'Generate some with: make simulate QPS=500',
          when: (data) => !data.totals.queries,
        }}
      >
        {(data) => (
          <>
            <section className="grid" aria-label="Summary">
              <Stat label="Queries" value={format.count(data.totals.queries)} />
              <Stat
                label="Error rate"
                value={format.percent(data.totals.error_rate)}
                note={`${format.count(data.totals.errors)} failed`}
                tone={errorTone(data.totals.error_rate)}
              />
              <Stat
                label="p95 latency"
                value={format.ms(data.totals.p95)}
                note={`p99 ${format.ms(data.totals.p99)}`}
                tone={latencyTone(data.totals.p99)}
              />
              <Stat label="Relevance" value={format.score(data.totals.relevance)} />
              <Stat label="Cache hits" value={format.percent(data.totals.cache_hit_rate, 1)} />
              <Stat
                label="Open anomalies"
                value={format.count(data.totals.open_anomalies)}
                tone={data.totals.open_anomalies ? 'warn' : 'good'}
                note={data.totals.open_anomalies ? 'see the anomalies view' : 'nothing detected'}
              />
            </section>

            <section className="card" style={{ marginTop: 'var(--gap)' }}>
              <h2>Services</h2>
              <p className="subtitle">Traffic and health for each reporting service</p>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Service</th>
                      <th scope="col" className="numeric">Queries</th>
                      <th scope="col" className="numeric">p95</th>
                      <th scope="col" className="numeric">Error rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.services.map((row) => (
                      <tr key={row.service}>
                        <td>{row.service}</td>
                        <td className="numeric">{format.count(row.queries)}</td>
                        <td className="numeric">{format.ms(row.p95)}</td>
                        <td className="numeric">
                          <span className={`badge ${errorTone(row.error_rate) === 'good' ? 'ok' : errorTone(row.error_rate)}`}>
                            {format.percent(row.error_rate)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </AsyncSection>

      <section className="panels">
        <div className="card">
          <h2>Latency p95</h2>
          <p className="subtitle">Averaged across rollups in each bucket — an approximation</p>
          <AsyncSection
            state={latency}
            empty={{ title: 'No latency data in this window', when: (d) => !d.series.length }}
          >
            {(data) => <LatencyChart series={data.series} percentile="p95" />}
          </AsyncSection>
        </div>

        <div className="card">
          <h2>Error rate</h2>
          <p className="subtitle">Share of queries that failed or timed out</p>
          <AsyncSection
            state={errors}
            empty={{ title: 'No errors recorded', when: (d) => !d.series.length }}
          >
            {(data) => <ErrorRateChart series={data.series} />}
          </AsyncSection>
        </div>

        <div className="card">
          <h2>Relevance</h2>
          <p className="subtitle">Mean score of returned results</p>
          <AsyncSection
            state={relevance}
            empty={{
              title: 'No relevance scores reported',
              hint: 'Only events that carry a score are counted',
              when: (d) => !d.series.length,
            }}
          >
            {(data) => <RelevanceChart series={data.series} />}
          </AsyncSection>
        </div>

        <div className="card">
          <h2>Query volume</h2>
          <p className="subtitle">Queries per bucket, by service</p>
          <AsyncSection
            state={latency}
            empty={{ title: 'No traffic in this window', when: (d) => !d.series.length }}
          >
            {(data) => <VolumeChart series={data.series} />}
          </AsyncSection>
        </div>
      </section>

      <section className="card" style={{ marginTop: 'var(--gap)' }}>
        <h2>Slowest queries</h2>
        <p className="subtitle">The way into a specific investigation</p>
        <AsyncSection
          state={slowest}
          empty={{ title: 'No queries in this window', when: (d) => !d.queries.length }}
        >
          {(data) => (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Query</th>
                    <th scope="col">Service</th>
                    <th scope="col" className="numeric">Latency</th>
                    <th scope="col">Status</th>
                    <th scope="col">When</th>
                    <th scope="col">Investigate</th>
                  </tr>
                </thead>
                <tbody>
                  {data.queries.map((row) => (
                    <tr key={row.query_id}>
                      <td className="wrap">{row.query}</td>
                      <td>{row.service}</td>
                      <td className="numeric">{format.ms(row.latency_ms)}</td>
                      <td>
                        <span className={`badge ${row.status === 'ok' ? 'ok' : 'critical'}`}>
                          {row.status}
                        </span>
                      </td>
                      <td>{format.time(row.timestamp)}</td>
                      <td>
                        <Link to={`/debug/${encodeURIComponent(row.query_id)}`}>Debug</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncSection>
      </section>
    </>
  )
}
