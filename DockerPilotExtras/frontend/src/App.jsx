import React from 'react'
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom'
import { ThemeProvider, useTheme } from './contexts/ThemeContext'
import { ServerProvider } from './contexts/ServerContext'
import ServerSelector from './components/ServerSelector'
import Pipelines from './pages/Pipelines'
import Deployments from './pages/Deployments'
import Environments from './pages/Environments'
import Status from './pages/Status'
import './App.css'

function Navigation() {
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()

  const navItems = [
    { path: '/', label: 'CI/CD Pipelines', component: Pipelines },
    { path: '/deployments', label: 'Deployments', component: Deployments },
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

function App() {
  return (
    <ThemeProvider>
      <ServerProvider>
        <Router>
          <div className="App">
            <Navigation />
            <main className="main-content">
              <Routes>
                <Route path="/" element={<Pipelines />} />
                <Route path="/deployments" element={<Deployments />} />
                <Route path="/environments" element={<Environments />} />
                <Route path="/status" element={<Status />} />
              </Routes>
            </main>
          </div>
        </Router>
      </ServerProvider>
    </ThemeProvider>
  )
}

export default App

