import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTheme } from '../contexts/ThemeContext'
import { useAuth } from '../contexts/AuthContext'
import { PRIMARY_NAV_ITEMS, isNavItemActive } from '../utils/navigation.mjs'
import ServerSelector from './ServerSelector'

function AppNavigation() {
  const location = useLocation()
  const { theme, toggleTheme } = useTheme()
  const { authEnabled, username, logout } = useAuth()
  const themeAction = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'

  return (
    <header className="app-header">
      <div className="nav-container">
        <Link className="nav-brand" to="/" aria-label="DockerPilot Extras home">
          <span>DockerPilot</span>
          <span className="nav-brand-product">Extras</span>
        </Link>

        <div className="nav-workspace">
          <nav className="nav-primary" aria-label="Primary navigation">
            <ul className="nav-menu">
              {PRIMARY_NAV_ITEMS.map((item) => {
                const active = isNavItemActive(location.pathname, item.path)
                return (
                  <li key={item.path}>
                    <Link
                      to={item.path}
                      className={active ? 'active' : undefined}
                      aria-current={active ? 'page' : undefined}
                    >
                      {item.label}
                    </Link>
                  </li>
                )
              })}
            </ul>
          </nav>

          <div className="nav-actions" role="group" aria-label="Application controls">
            {authEnabled && username && (
              <div className="nav-user">
                <span className="nav-username" title={username}>{username}</span>
                <button type="button" onClick={logout} className="shell-action nav-logout">
                  Logout
                </button>
              </div>
            )}
            <ServerSelector />
            <button
              type="button"
              onClick={toggleTheme}
              className="shell-action theme-toggle"
              aria-label={themeAction}
              title={themeAction}
            >
              <span aria-hidden="true">{theme === 'dark' ? '☀️' : '🌙'}</span>
            </button>
          </div>
        </div>
      </div>
    </header>
  )
}

export default AppNavigation
