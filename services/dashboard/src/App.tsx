import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { FilterBar, FiltersProvider } from './components/Filters'
import { AnomaliesPage } from './pages/Anomalies'
import { DebugPage } from './pages/Debug'
import { OverviewPage } from './pages/Overview'
import { TracesPage } from './pages/Traces'

export function App() {
  return (
    <FiltersProvider>
      <div className="app">
        <header className="masthead">
          <span className="brand">
            <span className="brand-dot" aria-hidden="true" />
            Search Metrics
          </span>

          <nav className="nav" aria-label="Main">
            <NavLink to="/overview">Overview</NavLink>
            <NavLink to="/anomalies">Anomalies</NavLink>
            <NavLink to="/traces">Traces</NavLink>
            <NavLink to="/debug">Debug</NavLink>
          </nav>

          <div className="masthead-controls">
            <FilterBar />
          </div>
        </header>

        <main>
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<OverviewPage />} />
            <Route path="/anomalies" element={<AnomaliesPage />} />
            <Route path="/traces" element={<TracesPage />} />
            <Route path="/traces/:traceId" element={<TracesPage />} />
            <Route path="/debug" element={<DebugPage />} />
            <Route path="/debug/:queryId" element={<DebugPage />} />
            <Route
              path="*"
              element={
                <div className="state">
                  <h3>No such page</h3>
                  <p>
                    <NavLink to="/overview">Back to the overview</NavLink>
                  </p>
                </div>
              }
            />
          </Routes>
        </main>
      </div>
    </FiltersProvider>
  )
}
