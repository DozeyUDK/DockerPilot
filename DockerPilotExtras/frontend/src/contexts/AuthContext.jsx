import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { authAPI } from '../services/api'

const AuthContext = createContext(null)

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

const defaultState = {
  checking: true,
  authEnabled: true,
  authenticated: false,
  username: null,
  mfaRequired: false,
  sessionIdleMinutes: 45,
  sessionExpiresInSeconds: null,
}

export const AuthProvider = ({ children }) => {
  const [state, setState] = useState(defaultState)

  const applyStatus = useCallback((payload = {}) => {
    setState(prev => ({
      ...prev,
      checking: false,
      authEnabled: payload.auth_enabled !== false,
      authenticated: Boolean(payload.authenticated),
      username: payload.username || null,
      mfaRequired: Boolean(payload.mfa_required),
      sessionIdleMinutes: payload.session_idle_minutes || prev.sessionIdleMinutes,
      sessionExpiresInSeconds: payload.session_expires_in_seconds ?? null,
    }))
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      const response = await authAPI.status()
      applyStatus(response.data || {})
      return response.data || {}
    } catch (error) {
      setState(prev => ({
        ...prev,
        checking: false,
        authEnabled: true,
        authenticated: false,
        username: null,
      }))
      return null
    }
  }, [applyStatus])

  useEffect(() => {
    refreshStatus()
  }, [refreshStatus])

  useEffect(() => {
    const intervalId = setInterval(() => {
      refreshStatus()
    }, 60_000)
    return () => clearInterval(intervalId)
  }, [refreshStatus])

  const login = useCallback(async (username, password, totpCode = '') => {
    try {
      const response = await authAPI.login(username, password, totpCode)
      const payload = response.data || {}
      applyStatus(payload)
      return { success: Boolean(payload.authenticated), payload }
    } catch (error) {
      const msg = error?.response?.data?.error || 'Login failed'
      return { success: false, error: msg }
    }
  }, [applyStatus])

  const logout = useCallback(async () => {
    try {
      await authAPI.logout()
    } catch (_error) {
      // Ignore API failure and clear local auth state anyway.
    } finally {
      setState(prev => ({
        ...prev,
        checking: false,
        authenticated: false,
        username: null,
      }))
    }
  }, [])

  const value = useMemo(() => ({
    ...state,
    refreshStatus,
    login,
    logout,
  }), [state, refreshStatus, login, logout])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
