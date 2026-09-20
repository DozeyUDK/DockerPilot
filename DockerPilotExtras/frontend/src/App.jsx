import React from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ServerProvider } from './contexts/ServerContext'
import AppNavigation from './components/AppNavigation'
import Login from './pages/Login'
import Pipelines from './pages/Pipelines'
import Environments from './pages/Environments'
import Status from './pages/Status'
import SecureDeploy from './pages/SecureDeploy'

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
        <Login />
      </div>
    )
  }

  return (
    <ServerProvider>
      <Router>
        <div className="App">
          <AppNavigation />
          <main className="main-content">
            <Routes>
              <Route path="/" element={<Pipelines />} />
              <Route path="/deployments" element={<Navigate to="/environments" replace />} />
              <Route path="/environments" element={<Environments />} />
              <Route path="/status" element={<Status />} />
              <Route path="/secure-deploy" element={<SecureDeploy />} />
            </Routes>
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
