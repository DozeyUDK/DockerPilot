import React, { useState } from 'react'
import { useAuth } from '../contexts/AuthContext'

function Login() {
  const { login, mfaRequired } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    const result = await login(username, password, totpCode)
    if (!result.success) {
      setError(result.error || 'Authentication failed')
    }
    setLoading(false)
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-title">
        <h1 className="auth-title" id="login-title">DockerPilot Extras</h1>
        <p className="auth-copy">
          Sign in to access CI/CD, environments and status operations.
        </p>

        {error && (
          <div className="alert alert-error auth-error" id="login-error" role="alert">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} aria-busy={loading}>
          <div className="form-field">
            <label htmlFor="login-username">Username</label>
            <input
              className="form-control"
              id="login-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              disabled={loading}
              autoComplete="username"
              autoFocus
            />
          </div>

          <div className="form-field">
            <label htmlFor="login-password">Password</label>
            <input
              className="form-control"
              id="login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={loading}
              autoComplete="current-password"
            />
          </div>

          {mfaRequired && (
            <div className="form-field">
              <label htmlFor="login-totp">MFA code (TOTP)</label>
              <input
                className="form-control"
                id="login-totp"
                type="text"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                required
                disabled={loading}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
              />
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn btn-primary form-button auth-submit"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </section>
    </main>
  )
}

export default Login
