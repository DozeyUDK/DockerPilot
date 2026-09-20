import React, { lazy, Suspense } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ServerProvider } from './contexts/ServerContext'
import AppNavigation from './components/AppNavigation'
import RouteErrorBoundary from './components/RouteErrorBoundary'

const Login = lazy(() => import('./pages/Login'))
const Pipelines = lazy(() => import('./pages/Pipelines'))
const Environments = lazy(() => import('./pages/Environments'))
const Status = lazy(() => import('./pages/Status'))
const SecureDeploy = lazy(() => import('./pages/SecureDeploy'))

function RouteLoading({ fullPage = false }) {
  const content = (
    <div className="card route-loading-card" role="status" aria-live="polite" aria-busy="true">
      <h2 className="card-title">Loading view...</h2>
      <p>Please wait.</p>
    </div>
  )

  if (fullPage) {
    return <main className="main-content route-loading route-loading-full">{content}</main>
  }

  return <div className="route-loading">{content}</div>
}

function AuthenticatedRoutes() {
  const location = useLocation()

  return (
    <RouteErrorBoundary resetKey={location.pathname}>
      <Suspense fallback={<RouteLoading />}>
        <Routes>
          <Route path="/" element={<Pipelines />} />
          <Route path="/deployments" element={<Navigate to="/environments" replace />} />
          <Route path="/environments" element={<Environments />} />
          <Route path="/status" element={<Status />} />
          <Route path="/secure-deploy" element={<SecureDeploy />} />
        </Routes>
      </Suspense>
    </RouteErrorBoundary>
  )
}

function AppShell() {
  const { checking, authEnabled, authenticated } = useAuth()

  if (checking) {
    return (
      <div className="App">
        <main className="main-content session-check">
          <div className="card session-check-card">
            <h3 className="card-title">Checking session...</h3>
            <p className="session-check-copy">Please wait.</p>
          </div>
        </main>
      </div>
    )
  }

  if (authEnabled && !authenticated) {
    return (
      <div className="App">
        <RouteErrorBoundary resetKey="login" fullPage>
          <Suspense fallback={<RouteLoading fullPage />}>
            <Login />
          </Suspense>
        </RouteErrorBoundary>
      </div>
    )
  }

  return (
    <ServerProvider>
      <Router>
        <div className="App">
          <AppNavigation />
          <main className="main-content">
            <AuthenticatedRoutes />
          </main>
        </div>
      </Router>
    </ServerProvider>
  )
}

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <AppShell />
      </AuthProvider>
    </ThemeProvider>
  )
}

export default App
