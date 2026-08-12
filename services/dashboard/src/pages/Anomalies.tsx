import { useState } from 'react'

import { api, type Anomaly } from '../api/client'
import { useFilters } from '../components/Filters'
import { AsyncSection } from '../components/States'
import { format } from '../components/Stat'
import { useQuery } from '../hooks/useQuery'

const SEVERITIES = ['', 'critical', 'warning', 'info'] as const

/** The z-score is the evidence; showing it turns an alert into an argument. */
function explain(anomaly: Anomaly): string {
  const direction = anomaly.z_score > 0 ? 'above' : 'below'
  return (
    `${anomaly.observed.toFixed(1)} is ${Math.abs(anomaly.z_score).toFixed(1)}σ ${direction} ` +
    `a baseline of ${anomaly.baseline_mean.toFixed(1)} ± ${anomaly.baseline_stddev.toFixed(1)}`
  )
}

export function AnomaliesPage() {
  const { minutes, service, refreshMs } = useFilters()
  const [severity, setSeverity] = useState<string>('')

  const anomalies = useQuery(
    () => api.anomalies({ minutes, service: service || undefined, severity: severity || undefined, limit: 200 }),
    [minutes, service, severity],
    refreshMs,
  )

  return (
    <section className="card">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h2>Anomalies</h2>
          <p className="subtitle">
            Windows that broke their own service&rsquo;s recent baseline
          </p>
        </div>
        <label className="control">
          <span>Severity</span>
          <select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
            aria-label="Severity filter"
          >
            {SEVERITIES.map((option) => (
              <option key={option || 'all'} value={option}>
                {option || 'All severities'}
              </option>
            ))}
          </select>
        </label>
      </div>

      <AsyncSection
        state={anomalies}
        empty={{
          title: 'No anomalies detected',
          hint: 'Try: make simulate SCENARIO=anomaly_spike',
          when: (data) => !data.anomalies.length,
        }}
      >
        {(data) => (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">Service</th>
                  <th scope="col">Metric</th>
                  <th scope="col">Evidence</th>
                  <th scope="col" className="numeric">z-score</th>
                  <th scope="col" className="numeric">Samples</th>
                  <th scope="col">Window</th>
                </tr>
              </thead>
              <tbody>
                {data.anomalies.map((anomaly) => (
                  <tr key={anomaly.anomaly_id}>
                    <td>
                      <span className={`badge ${anomaly.severity}`}>{anomaly.severity}</span>
                    </td>
                    <td>{anomaly.service}</td>
                    <td className="mono">{anomaly.metric}</td>
                    <td className="wrap">{explain(anomaly)}</td>
                    <td className="numeric">{anomaly.z_score.toFixed(1)}</td>
                    <td className="numeric">{format.count(anomaly.sample_count)}</td>
                    <td>{format.time(anomaly.window_start)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncSection>
    </section>
  )
}
