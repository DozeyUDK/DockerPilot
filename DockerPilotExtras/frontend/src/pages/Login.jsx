import React, { useState } from 'react'
import { useTheme } from '../contexts/ThemeContext'
import { useAuth } from '../contexts/AuthContext'

function Login() {
  const { theme } = useTheme()
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
    <div style={{
      minHeight: 'calc(100vh - 4rem)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '2rem 1rem',
    }}>
      <div style={{
        width: '100%',
        maxWidth: '420px',
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--border-color)',
        borderRadius: '10px',
        boxShadow: `0 10px 24px ${theme === 'dark' ? 'rgba(0, 0, 0, 0.5)' : 'rgba(0, 0, 0, 0.12)'}`,
        padding: '1.5rem',
      }}>
        <h2 style={{ marginTop: 0, marginBottom: '0.5rem', color: 'var(--text-primary)' }}>
          DockerPilotExtras Login
        </h2>
        <p style={{ marginTop: 0, marginBottom: '1rem', color: 'var(--text-secondary)' }}>
          Sign in to access CI/CD, environments and status operations.
        </p>

        {error && (
          <div style={{
            marginBottom: '1rem',
            padding: '0.75rem',
            borderRadius: '6px',
            border: '1px solid #dc3545',
            backgroundColor: theme === 'dark' ? 'rgba(220, 53, 69, 0.2)' : '#f8d7da',
            color: theme === 'dark' ? '#f87171' : '#721c24',
            fontSize: '0.9rem',
          }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          <label style={{ display: 'block', marginBottom: '0.4rem', color: 'var(--text-primary)' }}>
            Username
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
            style={{
              width: '100%',
              padding: '0.7rem',
              borderRadius: '6px',
              border: '1px solid var(--input-border)',
              backgroundColor: 'var(--input-bg)',
              color: 'var(--text-primary)',
              marginBottom: '0.9rem',
              boxSizing: 'border-box',
            }}
          />

          <label style={{ display: 'block', marginBottom: '0.4rem', color: 'var(--text-primary)' }}>
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
            style={{
              width: '100%',
              padding: '0.7rem',
              borderRadius: '6px',
              border: '1px solid var(--input-border)',
              backgroundColor: 'var(--input-bg)',
              color: 'var(--text-primary)',
              marginBottom: mfaRequired ? '0.9rem' : '1.2rem',
              boxSizing: 'border-box',
            }}
          />

          {mfaRequired && (
            <>
              <label style={{ display: 'block', marginBottom: '0.4rem', color: 'var(--text-primary)' }}>
                MFA code (TOTP)
              </label>
              <input
                type="text"
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                required
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                style={{
                  width: '100%',
                  padding: '0.7rem',
                  borderRadius: '6px',
                  border: '1px solid var(--input-border)',
                  backgroundColor: 'var(--input-bg)',
                  color: 'var(--text-primary)',
                  marginBottom: '1.2rem',
                  boxSizing: 'border-box',
                }}
              />
            </>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '0.75rem',
              borderRadius: '6px',
              border: 'none',
              backgroundColor: loading ? '#6c757d' : '#007bff',
              color: '#fff',
              fontWeight: '600',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default Login

