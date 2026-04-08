import React from 'react'
import { BrowserRouter as Router, Routes, Route, Link, Navigate, useLocation } from 'react-router-dom'
import { ThemeProvider, useTheme } from './contexts/ThemeContext'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ServerProvider } from './contexts/ServerContext'
import ServerSelector from './components/ServerSelector'
import Login from './pages/Login'
import Pipelines from './pages/Pipelines'
import Environments from './pages/Environments'
import Status from './pages/Status'
import './App.css'

function Navigation() {
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  const { authEnabled, username, logout } = useAuth()

  const navItems = [
    { path: '/', label: 'CI/CD Pipelines', component: Pipelines },
    { path: '/environments', label: 'Environments', component: Environments },
    { path: '/status', label: 'Status', component: Status }
  ]

  return (
    <nav className="navbar">
      <div className="nav-container">
        <div className="nav-brand">
          <h1>DockerPilot Web Panel</h1>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <ul className="nav-menu">
            {navItems.map((item) => (
              <li key={item.path}>
                <Link
                  to={item.path}
                  className={location.pathname === item.path ? 'active' : ''}
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
          {authEnabled && username && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'white' }}>
              <span style={{ fontSize: '0.9rem', opacity: 0.9 }}>
                {username}
              </span>
              <button
                type="button"
                onClick={logout}
                style={{
                  background: 'rgba(255, 255, 255, 0.2)',
                  border: '1px solid rgba(255, 255, 255, 0.3)',
                  borderRadius: '4px',
                  color: 'white',
                  padding: '0.35rem 0.6rem',
                  cursor: 'pointer',
                  fontSize: '0.85rem'
                }}
                title="Sign out"
              >
                Logout
              </button>
            </div>
          )}
          <ServerSelector />
          <button
            onClick={toggleTheme}
            className="theme-toggle"
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            style={{
              background: 'rgba(255, 255, 255, 0.2)',
              border: '1px solid rgba(255, 255, 255, 0.3)',
              borderRadius: '4px',
              padding: '0.5rem',
              cursor: 'pointer',
              fontSize: '1.2rem',
              color: 'white',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '40px',
              height: '40px',
              transition: 'all 0.3s'
            }}
          >
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
        </div>
      </div>
    </nav>
  )
}

function AppShell() {
  const { checking, authEnabled, authenticated } = useAuth()

  if (checking) {
    return (
      <div className="App">
        <main className="main-content" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div className="card" style={{ maxWidth: '420px', width: '100%', textAlign: 'center' }}>
            <h3 className="card-title">Checking session...</h3>
            <p style={{ color: 'var(--text-secondary)', marginBottom: 0 }}>Please wait.</p>
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
          <Navigation />
          <main className="main-content">
            <Routes>
              <Route path="/" element={<Pipelines />} />
              <Route path="/deployments" element={<Navigate to="/environments" replace />} />
              <Route path="/environments" element={<Environments />} />
              <Route path="/status" element={<Status />} />
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

