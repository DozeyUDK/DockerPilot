import React, { useState, useEffect, useRef } from 'react'
import { statusAPI, fileBrowserAPI } from '../services/api'
import { useTheme } from '../contexts/ThemeContext'
import { useServer } from '../contexts/ServerContext'
import '../App.css'

function Status() {
  const { theme } = useTheme()
  const [status, setStatus] = useState({
    docker: { available: false, version: null, error: null },
    dockerpilot: { available: false, version: null, error: null },
    context: { mode: 'local', server_name: 'Local', hostname: 'localhost' }
  })
  const [containerSummary, setContainerSummary] = useState(null)
  const [preflight, setPreflight] = useState(null)
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [cliProgram, setCliProgram] = useState('docker')
  const [cliCommand, setCliCommand] = useState('')
  const [cliOutput, setCliOutput] = useState([])
  const [cliLoading, setCliLoading] = useState(false)
  const [cliHistory, setCliHistory] = useState([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const [showHelp, setShowHelp] = useState(false)
  const [helpText, setHelpText] = useState('')
  const [loadingHelp, setLoadingHelp] = useState(false)
  const [workingDirectory, setWorkingDirectory] = useState('')
  const [showFileBrowser, setShowFileBrowser] = useState(false)
  const [browserPath, setBrowserPath] = useState('')
  const [browserItems, setBrowserItems] = useState([])
  const [loadingBrowser, setLoadingBrowser] = useState(false)
  const cliOutputRef = useRef(null)

  const { selectedServer } = useServer()

  useEffect(() => {
    checkStatus()
    loadContainers()
    loadPreflight()
  }, [selectedServer]) // Reload when server changes

  const checkStatus = async () => {
    setLoading(true)
    try {
      const response = await statusAPI.check()
      setStatus(response.data)
    } catch (error) {
      console.error('Error checking status:', error)
    } finally {
      setLoading(false)
    }
  }

  const loadContainers = async () => {
    try {
      const response = await statusAPI.containers()
      if (response.data.success) {
        setContainerSummary(response.data)
      } else {
        setContainerSummary({ error: response.data.error || 'Error loading status' })
      }
    } catch (error) {
      console.error('Error loading containers:', error)
      setContainerSummary({ error: 'Error loading container status' })
    }
  }

  const loadPreflight = async () => {
    setPreflightLoading(true)
    try {
      const response = await statusAPI.preflight()
      setPreflight(response.data)
    } catch (error) {
      setPreflight({
        success: false,
        checks: {},
        required_failed: [],
        warnings: [],
        error: error.response?.data?.error || error.message || 'Preflight check failed'
      })
    } finally {
      setPreflightLoading(false)
    }
  }

  const loadFileBrowser = async (path = '') => {
    setLoadingBrowser(true)
    try {
      const response = await fileBrowserAPI.browse(path)
      if (response.data.success) {
        setBrowserPath(response.data.current_path)
        setBrowserItems(response.data.items || [])
      }
    } catch (error) {
      console.error('Error loading file browser:', error)
    } finally {
      setLoadingBrowser(false)
    }
  }

  const openFileBrowser = () => {
    setShowFileBrowser(true)
    loadFileBrowser(workingDirectory || '')
  }

  const selectDirectoryFromBrowser = (dirPath) => {
    setWorkingDirectory(dirPath)
    setShowFileBrowser(false)
  }

  const executeCommand = async () => {
    if (!cliCommand.trim()) return
    
    setCliLoading(true)
    const commandToExecute = cliCommand.trim()
    
    // Add to history
    const newHistory = [...cliHistory, commandToExecute]
    setCliHistory(newHistory.slice(-50)) // Keep last 50 commands
    setHistoryIndex(-1)
    
    // Add command to output (with working directory info if set)
    const prompt = workingDirectory 
      ? `${cliProgram} $ [${workingDirectory}] ${commandToExecute}`
      : `${cliProgram} $ ${commandToExecute}`
    setCliOutput(prev => [...prev, { type: 'command', text: prompt }])
    
    try {
      const response = await statusAPI.executeCommand(
        cliProgram, 
        commandToExecute,
        workingDirectory || undefined
      )
      
      // Add output (even if return_code != 0, it may be useful)
      if (response.data.output) {
        setCliOutput(prev => [...prev, { type: 'output', text: response.data.output }])
      }
      if (response.data.error) {
        setCliOutput(prev => [...prev, { type: 'error', text: response.data.error }])
      }
      
      // Add suggestions if available
      if (response.data.suggestions) {
        const suggestions = response.data.suggestions
        let suggestionText = `\n💡 ${suggestions.message}\n`
        if (suggestions.commands) {
          suggestionText += suggestions.commands.map(cmd => `   • ${cmd}`).join('\n')
        }
        setCliOutput(prev => [...prev, { type: 'info', text: suggestionText }])
      }
      
      if (response.data.return_code !== 0) {
        setCliOutput(prev => [...prev, { 
          type: 'info', 
          text: `[Exit code: ${response.data.return_code}]` 
        }])
      }
      
      if (!response.data.success && !response.data.output && !response.data.error) {
        setCliOutput(prev => [...prev, { 
          type: 'error', 
          text: 'Command execution error' 
        }])
      }
    } catch (error) {
      setCliOutput(prev => [...prev, { 
        type: 'error', 
        text: error.response?.data?.error || error.message || 'Connection error' 
      }])
    } finally {
      setCliLoading(false)
      setCliCommand('')
      // Scroll to bottom
      setTimeout(() => {
        if (cliOutputRef.current) {
          cliOutputRef.current.scrollTop = cliOutputRef.current.scrollHeight
        }
      }, 100)
    }
  }

  const handleCliKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      executeCommand()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (cliHistory.length > 0) {
        const newIndex = historyIndex === -1 ? cliHistory.length - 1 : Math.max(0, historyIndex - 1)
        setHistoryIndex(newIndex)
        setCliCommand(cliHistory[newIndex])
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (historyIndex >= 0) {
        const newIndex = historyIndex + 1
        if (newIndex >= cliHistory.length) {
          setHistoryIndex(-1)
          setCliCommand('')
        } else {
          setHistoryIndex(newIndex)
          setCliCommand(cliHistory[newIndex])
        }
      }
    }
  }

  const clearCliOutput = () => {
    setCliOutput([])
  }

  const loadHelp = async () => {
    setLoadingHelp(true)
    try {
      const response = await statusAPI.getCommandHelp(cliProgram)
      if (response.data.success) {
        setHelpText(response.data.help)
        setShowHelp(true)
      }
    } catch (error) {
      console.error('Error loading help:', error)
    } finally {
      setLoadingHelp(false)
    }
  }

  const insertCommandSuggestion = (suggestion) => {
    setCliCommand(suggestion)
    setShowHelp(false)
  }

  const statusScopeLabel = status.context?.mode === 'remote'
    ? `Remote: ${status.context?.server_name || status.context?.hostname || 'Unknown server'}`
    : 'Local host'

  const preflightChecks = preflight?.checks ? Object.entries(preflight.checks) : []
  const preflightSummaryColor = !preflight
    ? '#6c757d'
    : (preflight.success
      ? (preflight.warnings?.length > 0 ? '#ffc107' : '#28a745')
      : '#dc3545')
  const preflightSummaryText = preflight?.success
    ? (preflight?.warnings?.length > 0
      ? `Required checks passed with ${preflight.warnings.length} warning(s)`
      : 'All required setup checks passed')
    : (preflight
      ? `Missing required checks: ${(preflight.required_failed || []).join(', ') || 'unknown'}`
      : 'Loading preflight status...')

  return (
    <div className="status-page">
      <h2>Status and Monitoring</h2>

      <div className="card">
        <div className="status-header-row" style={{ marginBottom: '1rem' }}>
          <h3 className="card-title">Connection Status</h3>
          <button className="btn btn-secondary" onClick={checkStatus} disabled={loading}>
            {loading ? 'Checking...' : 'Refresh'}
          </button>
        </div>

        <div
          className="status-scope-banner"
          style={{ backgroundColor: theme === 'dark' ? 'rgba(0, 123, 255, 0.18)' : '#e7f3ff' }}
        >
          Scope: <strong>{statusScopeLabel}</strong>
          {status.context?.hostname && status.context.mode === 'remote' && (
            <span style={{ color: 'var(--text-secondary)' }}> ({status.context.hostname})</span>
          )}
        </div>

        <div className="status-health-grid">
          {/* Docker Status */}
          <div style={{
            padding: '1rem',
            border: `2px solid ${status.docker.available ? '#28a745' : '#dc3545'}`,
            borderRadius: '4px',
            backgroundColor: status.docker.available 
              ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.15)' : '#d4edda')
              : (theme === 'dark' ? 'rgba(220, 53, 69, 0.15)' : '#f8d7da'),
            color: 'var(--text-primary)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <div style={{
                width: '12px',
                height: '12px',
                borderRadius: '50%',
                backgroundColor: status.docker.available ? '#28a745' : '#dc3545'
              }}></div>
              <strong style={{ 
                fontSize: '1.2rem', 
                fontWeight: '700',
                color: 'var(--text-primary)',
                textShadow: '0 1px 2px rgba(0,0,0,0.1)'
              }}>Docker</strong>
            </div>
            {status.docker.available ? (
              <div>
                <div style={{ 
                  color: theme === 'dark' ? '#4ade80' : '#155724',
                  fontWeight: '600',
                  fontSize: '1rem'
                }}>
                  ✓ Connected
                </div>
                {status.docker.version && (
                  <div className="status-wrap-anywhere" style={{ 
                    color: 'var(--text-secondary)', 
                    fontSize: '0.9rem', 
                    marginTop: '0.25rem' 
                  }}>
                    {status.docker.version}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ 
                color: theme === 'dark' ? '#f87171' : '#721c24',
                fontWeight: '600',
                fontSize: '1rem'
              }}>
                ✗ Not available
                {status.docker.error && (
                  <div className="status-wrap-anywhere" style={{ 
                    fontSize: '0.85rem', 
                    marginTop: '0.25rem',
                    color: 'var(--text-secondary)'
                  }}>
                    {status.docker.error}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* DockerPilot Status */}
          <div style={{
            padding: '1rem',
            border: `2px solid ${status.dockerpilot.available ? '#28a745' : '#dc3545'}`,
            borderRadius: '4px',
            backgroundColor: status.dockerpilot.available 
              ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.15)' : '#d4edda')
              : (theme === 'dark' ? 'rgba(220, 53, 69, 0.15)' : '#f8d7da'),
            color: 'var(--text-primary)'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <div style={{
                width: '12px',
                height: '12px',
                borderRadius: '50%',
                backgroundColor: status.dockerpilot.available ? '#28a745' : '#dc3545'
              }}></div>
              <strong style={{ 
                fontSize: '1.2rem', 
                fontWeight: '700',
                color: 'var(--text-primary)',
                textShadow: '0 1px 2px rgba(0,0,0,0.1)'
              }}>DockerPilot</strong>
            </div>
            {status.dockerpilot.available ? (
              <div>
                <div style={{ 
                  color: theme === 'dark' ? '#4ade80' : '#155724',
                  fontWeight: '600',
                  fontSize: '1rem'
                }}>
                  ✓ Connected
                </div>
                {status.dockerpilot.version && (
                  <div className="status-wrap-anywhere" style={{ 
                    color: 'var(--text-secondary)', 
                    fontSize: '0.9rem', 
                    marginTop: '0.25rem' 
                  }}>
                    {status.dockerpilot.version}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ 
                color: theme === 'dark' ? '#f87171' : '#721c24',
                fontWeight: '600',
                fontSize: '1rem'
              }}>
                ✗ Not available
                {status.dockerpilot.error && (
                  <div className="status-wrap-anywhere" style={{ 
                    fontSize: '0.85rem', 
                    marginTop: '0.25rem',
                    color: 'var(--text-secondary)'
                  }}>
                    {status.dockerpilot.error}
                  </div>
                )}
                <div style={{ 
                  fontSize: '0.85rem', 
                  marginTop: '0.5rem', 
                  color: 'var(--text-secondary)' 
                }}>
                  Install DockerPilot: https://github.com/DozeyUDK/DockerPilot
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="status-header-row" style={{ marginBottom: '1rem' }}>
          <h3 className="card-title">Setup Preflight</h3>
          <button className="btn btn-secondary" onClick={loadPreflight} disabled={preflightLoading}>
            {preflightLoading ? 'Checking...' : 'Refresh'}
          </button>
        </div>

        <div style={{
          marginBottom: '1rem',
          padding: '0.75rem',
          borderRadius: '4px',
          border: `1px solid ${preflightSummaryColor}`,
          backgroundColor: !preflight
            ? (theme === 'dark' ? 'rgba(108, 117, 125, 0.2)' : '#f1f3f5')
            : (theme === 'dark'
              ? (preflight.success ? 'rgba(40, 167, 69, 0.15)' : 'rgba(220, 53, 69, 0.15)')
              : (preflight.success ? '#d4edda' : '#f8d7da')),
          color: 'var(--text-primary)'
        }}>
          {preflight ? preflightSummaryText : 'Loading preflight status...'}
          <div className="status-wrap-anywhere" style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.35rem' }}>
            Preflight runs on the DockerPilotExtras host (local service dependencies).
          </div>
        </div>

        {preflight?.error && (
          <div style={{
            marginBottom: '1rem',
            padding: '0.75rem',
            borderRadius: '4px',
            border: '1px solid #dc3545',
            backgroundColor: theme === 'dark' ? 'rgba(220, 53, 69, 0.15)' : '#f8d7da',
            color: theme === 'dark' ? '#f87171' : '#721c24'
          }}>
            {preflight.error}
          </div>
        )}

        {preflightChecks.length > 0 ? (
          <div className="status-preflight-grid">
            {preflightChecks.map(([checkName, checkData]) => (
              <div
                key={checkName}
                style={{
                  padding: '0.75rem',
                  borderRadius: '4px',
                  border: `1px solid ${checkData.ok ? '#28a745' : (checkData.required ? '#dc3545' : '#ffc107')}`,
                  backgroundColor: theme === 'dark'
                    ? (checkData.ok
                      ? 'rgba(40, 167, 69, 0.12)'
                      : (checkData.required ? 'rgba(220, 53, 69, 0.15)' : 'rgba(255, 193, 7, 0.14)'))
                    : (checkData.ok ? '#f4fff7' : (checkData.required ? '#fff3f3' : '#fff9e6'))
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem', alignItems: 'center' }}>
                  <strong className="status-wrap-anywhere" style={{ color: 'var(--text-primary)' }}>{checkName}</strong>
                  <span style={{
                    fontWeight: 'bold',
                    color: checkData.ok ? '#28a745' : (checkData.required ? '#dc3545' : '#856404')
                  }}>
                    {checkData.ok ? 'OK' : (checkData.required ? 'REQUIRED' : 'WARNING')}
                  </span>
                </div>
                <div className="status-wrap-anywhere" style={{ color: 'var(--text-secondary)', marginTop: '0.35rem', fontSize: '0.9rem' }}>
                  {checkData.details || 'No details'}
                </div>
                {checkData.minimum && (
                  <div style={{ color: 'var(--text-tertiary)', marginTop: '0.25rem', fontSize: '0.8rem' }}>
                    Minimum: {checkData.minimum}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: '#666', textAlign: 'center', padding: '1rem' }}>
            {preflightLoading ? 'Running checks...' : 'No preflight details'}
          </p>
        )}
      </div>

      <div className="card">
        <div className="status-header-row" style={{ marginBottom: '1rem' }}>
          <h3 className="card-title">Pipeline Status and Containers</h3>
          <button className="btn btn-secondary" onClick={loadContainers}>
            Refresh
          </button>
        </div>

        {containerSummary ? (
          containerSummary.error ? (
            <div className="status-alert-error">
              <strong>❌ Error:</strong> {containerSummary.error}
            </div>
          ) : containerSummary.summary ? (
            <div>
              <div className="status-stat-grid">
                <div className="status-summary-card total">
                  <div className="status-summary-value" style={{ color: '#007bff' }}>
                    {containerSummary.summary.total}
                  </div>
                  <div className="status-summary-label">Total containers</div>
                </div>

                <div className="status-summary-card running">
                  <div className="status-summary-value" style={{ color: '#28a745' }}>
                    {containerSummary.summary.running}
                  </div>
                  <div className="status-summary-label">Running</div>
                </div>

                <div className="status-summary-card stopped">
                  <div className="status-summary-value" style={{ color: '#856404' }}>
                    {containerSummary.summary.stopped}
                  </div>
                  <div className="status-summary-label">Stopped</div>
                </div>
              </div>

              {containerSummary.summary.total === 0 ? (
                <div className="status-empty" style={{ fontStyle: 'italic' }}>
                  No containers
                </div>
              ) : (
                <div className={`status-container-result ${containerSummary.summary.stopped === 0 ? 'ok' : 'warn'}`}>
                  {containerSummary.summary.stopped === 0 ? (
                    <span style={{ color: '#155724', fontWeight: 'bold' }}>
                      ✅ All containers are running correctly
                    </span>
                  ) : (
                    <span style={{ color: '#856404' }}>
                      ⚠️ {containerSummary.summary.stopped} container(s) stopped
                    </span>
                  )}
                </div>
              )}

              {containerSummary.containers && containerSummary.containers.length > 0 && (
                <div style={{ marginTop: '1rem' }}>
                  <strong style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>Recent containers:</strong>
                  <div className="status-container-list">
                    {containerSummary.containers.map((container) => (
                      <div
                        key={container.id || container.name}
                        className="status-container-row"
                      >
                        <span className="status-container-name">{container.name}</span>
                        <span className={`status-container-state ${container.state === 'running' ? 'running' : 'stopped'}`}>
                          {container.state === 'running' ? '● Running' : '○ Stopped'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="status-empty">
              No container data
            </p>
          )
        ) : (
          <p className="status-empty">
            Loading container status...
          </p>
        )}
      </div>

      <div className="card status-cli-card">
        <div className="status-header-row" style={{ marginBottom: '1rem' }}>
          <h3 className="card-title">Interactive CLI</h3>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button 
              className="btn btn-secondary" 
              onClick={clearCliOutput}
              style={{ fontSize: '0.85rem', padding: '0.25rem 0.5rem' }}
            >
              Clear
            </button>
          </div>
        </div>

        {/* Program Switcher and Working Directory */}
        <div className="status-cli-controls">
          <div className="status-cli-row">
            <label style={{ fontWeight: 'bold' }}>Program:</label>
            <div className="status-cli-program-buttons">
              <button
                onClick={() => {
                  setCliProgram('docker')
                  setShowHelp(false)
                }}
                className={`status-cli-toggle ${cliProgram === 'docker' ? 'active' : ''}`}
              >
                Docker
              </button>
              <button
                onClick={() => {
                  setCliProgram('dockerpilot')
                  setShowHelp(false)
                }}
                className={`status-cli-toggle ${cliProgram === 'dockerpilot' ? 'active' : ''}`}
              >
                DockerPilot
              </button>
            </div>
            <button
              onClick={loadHelp}
              disabled={loadingHelp}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor: '#6c757d',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: loadingHelp ? 'not-allowed' : 'pointer',
                fontSize: '0.9rem'
              }}
              title="Show available commands"
            >
              {loadingHelp ? '⏳' : '❓'} Help
            </button>
          </div>
          
          {/* Working Directory Selector */}
          <div className="status-cli-row">
            <label style={{ fontWeight: 'bold' }}>Working directory:</label>
            <input
              type="text"
              value={workingDirectory}
              onChange={(e) => setWorkingDirectory(e.target.value)}
              placeholder="Empty = default directory"
              className="status-cli-input"
            />
            <button
              onClick={openFileBrowser}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor: '#28a745',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                fontSize: '0.9rem'
              }}
              title="Browse directories"
            >
              📁 Browse
            </button>
            {workingDirectory && (
              <button
                onClick={() => setWorkingDirectory('')}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: '#dc3545',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '0.9rem'
                }}
                title="Clear working directory"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Help Modal */}
        {showHelp && helpText && (
          <div style={{
            marginBottom: '1rem',
            padding: '1rem',
            backgroundColor: '#f8f9fa',
            border: '1px solid #dee2e6',
            borderRadius: '4px',
            maxHeight: '300px',
            overflowY: 'auto'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
              <strong>Available commands for {cliProgram}:</strong>
              <button
                onClick={() => setShowHelp(false)}
                style={{
                  background: 'none',
                  border: 'none',
                  fontSize: '1.2rem',
                  cursor: 'pointer',
                  padding: '0 0.5rem'
                }}
              >
                ×
              </button>
            </div>
            <div style={{
              fontFamily: 'Consolas, Monaco, "Courier New", monospace',
              fontSize: '0.85rem',
              whiteSpace: 'pre-wrap',
              color: '#333'
            }}>
              {helpText}
            </div>
            {/* Quick suggestions for DockerPilot */}
            {cliProgram === 'dockerpilot' && (
              <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid #dee2e6' }}>
                <strong style={{ fontSize: '0.9rem' }}>Szybkie sugestie:</strong>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
                  {['container list', 'container status', 'monitor', 'deploy', 'validate', 'backup'].map((cmd) => (
                    <button
                      key={cmd}
                      onClick={() => insertCommandSuggestion(cmd)}
                      style={{
                        padding: '0.25rem 0.5rem',
                        backgroundColor: '#007bff',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '0.85rem'
                      }}
                    >
                      {cmd}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* CLI Output */}
        <div
          ref={cliOutputRef}
          className="cli-output-container status-cli-output"
          style={{
            fontFamily: '"Consolas", "Monaco", "Courier New", "Liberation Mono", "DejaVu Sans Mono", monospace',
            overflowY: 'auto',
            overflowX: 'auto',
            wordBreak: 'keep-all',
            overflowWrap: 'normal'
          }}
        >
          {cliOutput.length === 0 ? (
            <div style={{ color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap' }}>
              Type command below and press Enter to execute...
              <br />
              Use ↑↓ arrows to navigate command history
            </div>
          ) : (
            <pre style={{
              margin: 0,
              padding: 0,
              fontFamily: '"Consolas", "Monaco", "Courier New", "Liberation Mono", "DejaVu Sans Mono", monospace',
              fontSize: '0.8rem',
              whiteSpace: 'pre',
              wordBreak: 'keep-all',
              overflowWrap: 'normal',
              wordWrap: 'normal',
              display: 'block',
              color: 'var(--text-primary)',
              backgroundColor: 'transparent',
              fontVariantLigatures: 'none',
              fontFeatureSettings: 'normal',
              unicodeBidi: 'embed',
              lineHeight: '1.2',
              letterSpacing: '0px'
            }}>
              {cliOutput.map((item, idx) => {
                // For ASCII tables, render as plain text without coloring
                const isTableOutput = item.text.includes('┏') || item.text.includes('┡') || 
                                     item.text.includes('┃') ||
                                     (item.text.includes('│') && item.text.length > 50);
                
                if (isTableOutput) {
                // For tables, render with formatting preserved
                  return (
                    <span 
                      key={idx} 
                      style={{ 
                        whiteSpace: 'pre',
                        fontFamily: 'inherit',
                        display: 'block',
                        color: '#d4d4d4'
                      }}
                    >
                      {item.text}
                    </span>
                  );
                }
                
                // For other text, apply coloring
                const color = item.type === 'command' ? '#4ec9b0' : 
                             item.type === 'error' ? '#f48771' : 
                             item.type === 'info' ? '#ce9178' : '#d4d4d4';
                
                return <span key={idx} style={{ color }}>{item.text}</span>;
              })}
            </pre>
          )}
          {cliLoading && (
            <div style={{ color: '#888' }}>
              <span style={{ animation: 'blink 1s infinite' }}>▋</span> Executing...
            </div>
          )}
        </div>

        {/* CLI Input */}
        <div className="status-cli-row" style={{ width: '100%' }}>
          <span className="status-cli-prompt">
            {cliProgram} $
          </span>
          <input
            type="text"
            value={cliCommand}
            onChange={(e) => setCliCommand(e.target.value)}
            onKeyDown={handleCliKeyPress}
            placeholder="Type command..."
            disabled={cliLoading}
            className="status-cli-input"
            style={{
              fontFamily: 'Consolas, Monaco, "Courier New", monospace',
              backgroundColor: cliLoading ? 'var(--bg-tertiary)' : 'var(--input-bg)',
              minWidth: 0 // Allows shrinking
            }}
          />
          <button
            className="btn btn-primary"
            onClick={executeCommand}
            disabled={cliLoading || !cliCommand.trim()}
            style={{ flexShrink: 0 }}
          >
            {cliLoading ? '⏳' : '▶'}
          </button>
        </div>
        <small style={{ color: '#666', fontSize: '0.85rem', marginTop: '0.5rem', display: 'block' }}>
          Enter - run | ↑↓ - history | Ctrl+L - clear
        </small>
      </div>

      {/* File Browser Modal for Working Directory */}
      {showFileBrowser && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0,0,0,0.5)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 2000
        }} onClick={() => setShowFileBrowser(false)}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '1.5rem',
            maxWidth: '600px',
            maxHeight: '80vh',
            width: '90%',
            overflow: 'auto',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ color: 'var(--text-primary)' }}>Select working directory</h3>
              <button 
                onClick={() => setShowFileBrowser(false)}
                style={{ 
                  background: 'none', 
                  border: 'none', 
                  fontSize: '1.5rem', 
                  cursor: 'pointer',
                  padding: '0 0.5rem',
                  color: 'var(--text-primary)'
                }}
              >
                ×
              </button>
            </div>
            
            <div style={{ marginBottom: '1rem' }}>
              <div style={{ 
                display: 'flex', 
                gap: '0.5rem', 
                alignItems: 'center',
                marginBottom: '0.5rem'
              }}>
                <button 
                  onClick={() => {
                    const parentPath = browserPath.split('/').slice(0, -1).join('/') || '/'
                    loadFileBrowser(parentPath)
                  }}
                  disabled={!browserPath || browserPath === '/' || browserPath.split('/').length <= 1}
                  style={{ padding: '0.25rem 0.5rem' }}
                >
                  ↑ Back
                </button>
                <input
                  type="text"
                  value={browserPath}
                  onChange={(e) => setBrowserPath(e.target.value)}
                  onKeyPress={(e) => {
                    if (e.key === 'Enter') {
                      loadFileBrowser(browserPath)
                    }
                  }}
                  style={{ 
                    flex: 1, 
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px'
                  }}
                  placeholder="Enter path..."
                />
                <button 
                  onClick={() => loadFileBrowser(browserPath)}
                  style={{ padding: '0.5rem 1rem' }}
                >
                  Go
                </button>
              </div>
            </div>

            {loadingBrowser ? (
              <div style={{ textAlign: 'center', padding: '2rem' }}>Loading...</div>
            ) : (
              <div style={{ 
                border: '1px solid var(--border-color)', 
                borderRadius: '4px',
                maxHeight: '400px',
                overflowY: 'auto',
                backgroundColor: 'var(--bg-tertiary)'
              }}>
                {browserItems.length === 0 ? (
                  <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                    Empty directory
                  </div>
                ) : (
                  browserItems.map((item, idx) => (
                    <div
                      key={idx}
                      onClick={() => {
                        if (item.is_dir) {
                          loadFileBrowser(item.path)
                        }
                      }}
                      style={{
                        padding: '0.75rem',
                        cursor: item.is_dir ? 'pointer' : 'default',
                        borderBottom: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        backgroundColor: item.is_dir 
                          ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.2)' : '#e8f5e9')
                          : 'var(--card-bg)',
                        opacity: item.is_dir ? 1 : 0.6,
                        color: 'var(--text-primary)'
                      }}
                      onMouseEnter={(e) => {
                        if (item.is_dir) {
                          e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                        }
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = item.is_dir 
                          ? (theme === 'dark' ? 'rgba(40, 167, 69, 0.2)' : '#e8f5e9')
                          : 'var(--card-bg)'
                      }}
                    >
                      <span style={{ fontSize: '1.2rem' }}>
                        {item.is_dir ? '📁' : '📄'}
                      </span>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: item.is_dir ? 'bold' : 'normal' }}>
                          {item.name}
                        </div>
                        {item.is_file && (
                          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                            {(item.size / 1024).toFixed(2)} KB
                          </div>
                        )}
                      </div>
                      {item.is_dir && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            selectDirectoryFromBrowser(item.path)
                          }}
                          style={{
                            padding: '0.25rem 0.5rem',
                            backgroundColor: '#28a745',
                            color: 'white',
                            border: 'none',
                            borderRadius: '4px',
                            cursor: 'pointer'
                          }}
                        >
                          Select
                        </button>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default Status

