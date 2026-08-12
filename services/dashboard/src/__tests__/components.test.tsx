/**
 * Component tests for the pieces where being wrong would mislead an operator:
 * the numbers, the chart pivot, the waterfall geometry, and the states that
 * tell someone what to do when there is nothing to show.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, type Trace } from '../api/client'
import { pivot } from '../components/Charts'
import { AsyncSection, Empty, Failure } from '../components/States'
import { Stat, errorTone, format, latencyTone } from '../components/Stat'
import { Waterfall, layout } from '../components/Waterfall'

afterEach(() => vi.restoreAllMocks())

describe('number formatting', () => {
  it('renders an absent value as an em dash rather than zero', () => {
    // Zero and "no data" mean very different things on an operations dashboard.
    expect(format.ms(null)).toBe('—')
    expect(format.count(null)).toBe('—')
    expect(format.percent(null)).toBe('—')
    expect(format.score(undefined)).toBe('—')
  })

  it('switches milliseconds to seconds once they stop being readable', () => {
    expect(format.ms(42.4)).toBe('42 ms')
    expect(format.ms(9.2)).toBe('9.2 ms')
    expect(format.ms(1500)).toBe('1.50 s')
  })

  it('renders rates as percentages', () => {
    expect(format.percent(0.0123)).toBe('1.23%')
    expect(format.percent(0.5, 0)).toBe('50%')
  })

  it('groups large counts', () => {
    expect(format.count(1234567)).toMatch(/1.234.567/)
  })
})

describe('thresholds', () => {
  it('escalates tone as the error rate rises', () => {
    expect(errorTone(0.001)).toBe('good')
    expect(errorTone(0.02)).toBe('warn')
    expect(errorTone(0.2)).toBe('bad')
  })

  it('escalates tone as p99 rises', () => {
    expect(latencyTone(200)).toBe('good')
    expect(latencyTone(1500)).toBe('warn')
    expect(latencyTone(4000)).toBe('bad')
  })

  it('has no opinion about a missing value', () => {
    expect(errorTone(null)).toBe('neutral')
    expect(latencyTone(undefined)).toBe('neutral')
  })
})

describe('Stat', () => {
  it('shows its label, value and note', () => {
    render(<Stat label="Error rate" value="1.20%" note="12 failed" tone="warn" />)

    expect(screen.getByText('Error rate')).toBeInTheDocument()
    expect(screen.getByText('1.20%')).toBeInTheDocument()
    expect(screen.getByText('12 failed')).toBeInTheDocument()
  })
})

describe('chart pivot', () => {
  const series = [
    { bucket: '10:00', service: 'search-api', p95: 100 },
    { bucket: '10:00', service: 'ranking-service', p95: 50 },
    { bucket: '10:01', service: 'search-api', p95: 120 },
  ]

  it('turns one row per service into one row per bucket', () => {
    const { rows, services } = pivot(series, (row) => row.p95)

    expect(services).toEqual(['ranking-service', 'search-api'])
    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({ bucket: '10:00', 'search-api': 100, 'ranking-service': 50 })
  })

  it('keeps buckets in time order regardless of input order', () => {
    const { rows } = pivot([...series].reverse(), (row) => row.p95)
    expect(rows.map((row) => row.bucket)).toEqual(['10:00', '10:01'])
  })

  it('leaves a service absent from a bucket undefined rather than zero', () => {
    // A zero would draw a line to the floor and read as an outage.
    const { rows } = pivot(series, (row) => row.p95)
    expect(rows[1]['ranking-service']).toBeUndefined()
  })
})

const TRACE: Trace = {
  trace_id: 'trace-1',
  span_count: 3,
  orphan_count: 0,
  total_duration_ms: 1000,
  services: ['search-api', 'ranking-service'],
  critical_path: [{ service: 'search-api', operation: 'GET /search', duration_ms: 1000 }],
  roots: [
    {
      span_id: 'a',
      parent_span_id: '',
      service: 'search-api',
      operation: 'GET /search',
      start_time: '2026-08-12T10:00:00.000Z',
      duration_ms: 1000,
      self_time_ms: 100,
      status: 'ok',
      depth: 0,
      orphaned: false,
      attributes: {},
      children: [
        {
          span_id: 'b',
          parent_span_id: 'a',
          service: 'ranking-service',
          operation: 'rank',
          start_time: '2026-08-12T10:00:00.500Z',
          duration_ms: 400,
          self_time_ms: 400,
          status: 'error',
          depth: 1,
          orphaned: false,
          attributes: {},
          children: [],
        },
      ],
    },
  ],
}

describe('trace waterfall', () => {
  it('flattens the tree in visual order', () => {
    const { rows } = layout(TRACE)
    expect(rows.map((row) => row.span.span_id)).toEqual(['a', 'b'])
  })

  it('positions a child by when it actually started', () => {
    // A child that begins halfway through its parent must be drawn halfway
    // along, or the waterfall tells a false story about concurrency.
    const { rows, startMs, totalMs } = layout(TRACE)
    const child = rows[1]

    expect(((child.offsetMs - startMs) / totalMs) * 100).toBeCloseTo(50, 0)
  })

  it('renders every span with its duration', () => {
    render(<Waterfall trace={TRACE} />)

    expect(screen.getByText('search-api')).toBeInTheDocument()
    expect(screen.getByText('ranking-service')).toBeInTheDocument()
    expect(screen.getByText('1.00 s')).toBeInTheDocument()
    expect(screen.getByText('400 ms')).toBeInTheDocument()
  })

  it('marks a failed span differently from a healthy one', () => {
    const { container } = render(<Waterfall trace={TRACE} />)
    expect(container.querySelectorAll('.waterfall-bar.error')).toHaveLength(1)
  })
})

describe('states', () => {
  it('tells the reader what to do when there is nothing to show', () => {
    render(<Empty title="No traffic in this window" hint="Try: make simulate QPS=500" />)

    expect(screen.getByText('No traffic in this window')).toBeInTheDocument()
    expect(screen.getByText(/make simulate/)).toBeInTheDocument()
  })

  it('distinguishes an unreachable gateway from a bad response', () => {
    const { rerender } = render(<Failure error={new ApiError('boom', 0)} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/unreachable/i)

    rerender(<Failure error={new ApiError('clickhouse died', 503)} />)
    expect(screen.getByRole('alert')).toHaveTextContent(/analytics store/i)
  })

  it('offers a retry that calls back', async () => {
    const onRetry = vi.fn()
    render(<Failure error={new ApiError('boom', 500)} onRetry={onRetry} />)

    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

describe('AsyncSection', () => {
  const base = { data: null, error: null, loading: false, reload: () => {} }

  it('shows a loading state first', () => {
    render(
      <AsyncSection state={{ ...base, loading: true }}>{() => <p>content</p>}</AsyncSection>,
    )
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('prefers the error over stale data', () => {
    render(
      <AsyncSection state={{ ...base, data: { x: 1 }, error: new ApiError('nope', 500) }}>
        {() => <p>content</p>}
      </AsyncSection>,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.queryByText('content')).not.toBeInTheDocument()
  })

  it('shows the empty state when the predicate says the data is empty', () => {
    render(
      <AsyncSection
        state={{ ...base, data: { series: [] } }}
        empty={{ title: 'Nothing here', when: (data) => !data.series.length }}
      >
        {() => <p>content</p>}
      </AsyncSection>,
    )
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
  })

  it('renders the children once there is data', () => {
    render(
      <AsyncSection state={{ ...base, data: { series: [1] } }}>
        {() => <p>content</p>}
      </AsyncSection>,
    )
    expect(screen.getByText('content')).toBeInTheDocument()
  })
})

describe('app shell', () => {
  it('renders navigation and the filter controls', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ window: {}, totals: {}, services: [] }), {
        headers: { 'content-type': 'application/json' },
      }),
    )
    const { App } = await import('../App')

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
    expect(screen.getByLabelText('Time range')).toBeInTheDocument()
    expect(screen.getByLabelText('Service filter')).toBeInTheDocument()
    expect(screen.getByLabelText('Auto refresh interval')).toBeInTheDocument()
  })

  it('changing the range refetches with the new window', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ window: {}, totals: {}, services: [], series: [], queries: [] }), {
        headers: { 'content-type': 'application/json' },
      }),
    )
    const { App } = await import('../App')

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <App />
      </MemoryRouter>,
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    fetchMock.mockClear()

    await userEvent.selectOptions(screen.getByLabelText('Time range'), '1440')

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]))
      expect(urls.some((url) => url.includes('minutes=1440'))).toBe(true)
    })
  })
})
