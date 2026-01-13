import React, { useState, useEffect, useRef } from 'react'
import { statusAPI, fileBrowserAPI } from '../services/api'
import { useTheme } from '../contexts/ThemeContext'
import { useServer } from '../contexts/ServerContext'
import '../App.css'

function Status() {
  const { theme } = useTheme()
  const [status, setStatus] = useState({
    docker: { available: false, version: null, error: null },
    dockerpilot: { available: false, version: null, error: null }
  })
  const [containerSummary, setContainerSummary] = useState(null)
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

  return (
    <div>
      <h2>Status and Monitoring</h2>

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 className="card-title">Connection Status</h3>
          <button className="btn btn-secondary" onClick={checkStatus} disabled={loading}>
            {loading ? 'Checking...' : 'Refresh'}
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
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
                  <div style={{ 
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
                  <div style={{ 
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
                  <div style={{ 
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
                  <div style={{ 
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 className="card-title">Status Pipeline i Kontenery</h3>
          <button className="btn btn-secondary" onClick={loadContainers}>
            Refresh
          </button>
        </div>

        {containerSummary ? (
          containerSummary.error ? (
            <div style={{
              padding: '1rem',
              backgroundColor: '#f8d7da',
              color: '#721c24',
              borderRadius: '4px',
              border: '1px solid #f5c6cb'
            }}>
              <strong>❌ Błąd:</strong> {containerSummary.error}
            </div>
          ) : containerSummary.summary ? (
            <div>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                gap: '1rem',
                marginBottom: '1rem'
              }}>
                <div style={{
                  padding: '1rem',
                  backgroundColor: '#e7f3ff',
                  borderRadius: '4px',
                  border: '2px solid #007bff',
                  textAlign: 'center'
                }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: '#007bff' }}>
                    {containerSummary.summary.total}
                  </div>
                  <div style={{ color: '#666', fontSize: '0.9rem' }}>Wszystkich kontenerów</div>
                </div>
                
                <div style={{
                  padding: '1rem',
                  backgroundColor: '#d4edda',
                  borderRadius: '4px',
                  border: '2px solid #28a745',
                  textAlign: 'center'
                }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: '#28a745' }}>
                    {containerSummary.summary.running}
                  </div>
                  <div style={{ color: '#666', fontSize: '0.9rem' }}>Działa</div>
                </div>
                
                <div style={{
                  padding: '1rem',
                  backgroundColor: '#fff3cd',
                  borderRadius: '4px',
                  border: '2px solid #ffc107',
                  textAlign: 'center'
                }}>
                  <div style={{ fontSize: '2rem', fontWeight: 'bold', color: '#856404' }}>
                    {containerSummary.summary.stopped}
                  </div>
                  <div style={{ color: '#666', fontSize: '0.9rem' }}>Zatrzymanych</div>
                </div>
              </div>

              {containerSummary.summary.total === 0 ? (
                <div style={{
                  padding: '1rem',
                  textAlign: 'center',
                  color: '#666',
                  fontStyle: 'italic'
                }}>
                  Brak kontenerów
                </div>
              ) : (
                <div style={{
                  padding: '0.75rem',
                  backgroundColor: containerSummary.summary.stopped === 0 ? '#d4edda' : '#fff3cd',
                  borderRadius: '4px',
                  border: `1px solid ${containerSummary.summary.stopped === 0 ? '#28a745' : '#ffc107'}`,
                  textAlign: 'center'
                }}>
                  {containerSummary.summary.stopped === 0 ? (
                    <span style={{ color: '#155724', fontWeight: 'bold' }}>
                      ✅ Wszystkie kontenery działają poprawnie
                    </span>
                  ) : (
                    <span style={{ color: '#856404' }}>
                      ⚠️ {containerSummary.summary.stopped} kontener(ów) zatrzymanych
                    </span>
                  )}
                </div>
              )}

              {containerSummary.containers && containerSummary.containers.length > 0 && (
                <div style={{ marginTop: '1rem' }}>
                  <strong style={{ fontSize: '0.9rem', color: '#666' }}>Recent containers:</strong>
                  <div style={{
                    marginTop: '0.5rem',
                    maxHeight: '200px',
                    overflowY: 'auto',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    padding: '0.5rem'
                  }}>
                    {containerSummary.containers.map((container, idx) => (
                      <div
                        key={idx}
                        style={{
                          padding: '0.5rem',
                          borderBottom: idx < containerSummary.containers.length - 1 ? '1px solid #eee' : 'none',
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center'
                        }}
                      >
                        <span style={{ fontWeight: '500' }}>{container.name}</span>
                        <span style={{
                          fontSize: '0.85rem',
                          color: container.state === 'running' ? '#28a745' : '#6c757d',
                          fontWeight: container.state === 'running' ? 'bold' : 'normal'
                        }}>
                          {container.state === 'running' ? '● Działa' : '○ Zatrzymany'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p style={{ color: '#666', textAlign: 'center', padding: '2rem' }}>
              Brak danych o kontenerach
            </p>
          )
        ) : (
          <p style={{ color: '#666', textAlign: 'center', padding: '2rem' }}>
            Loading container status...
          </p>
        )}
      </div>

      <div className="card" style={{ width: '100%', maxWidth: '100%', overflow: 'visible' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 className="card-title">Interaktywny CLI</h3>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button 
              className="btn btn-secondary" 
              onClick={clearCliOutput}
              style={{ fontSize: '0.85rem', padding: '0.25rem 0.5rem' }}
            >
              Wyczyść
            </button>
          </div>
        </div>

        {/* Program Switcher and Working Directory */}
        <div style={{ marginBottom: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontWeight: 'bold' }}>Program:</label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                onClick={() => {
                  setCliProgram('docker')
                  setShowHelp(false)
                }}
                style={{
                  padding: '0.5rem 1rem',
                  border: '2px solid',
                  borderColor: cliProgram === 'docker' ? '#007bff' : '#ccc',
                  backgroundColor: cliProgram === 'docker' ? '#007bff' : 'white',
                  color: cliProgram === 'docker' ? 'white' : '#333',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: cliProgram === 'docker' ? 'bold' : 'normal'
                }}
              >
                Docker
              </button>
              <button
                onClick={() => {
                  setCliProgram('dockerpilot')
                  setShowHelp(false)
                }}
                style={{
                  padding: '0.5rem 1rem',
                  border: '2px solid',
                  borderColor: cliProgram === 'dockerpilot' ? '#007bff' : '#ccc',
                  backgroundColor: cliProgram === 'dockerpilot' ? '#007bff' : 'white',
                  color: cliProgram === 'dockerpilot' ? 'white' : '#333',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: cliProgram === 'dockerpilot' ? 'bold' : 'normal'
                }}
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
              {loadingHelp ? '⏳' : '❓'} Pomoc
            </button>
          </div>
          
          {/* Working Directory Selector */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ fontWeight: 'bold' }}>Katalog roboczy:</label>
            <input
              type="text"
              value={workingDirectory}
              onChange={(e) => setWorkingDirectory(e.target.value)}
              placeholder="Empty = default directory"
              style={{
                flex: 1,
                minWidth: '200px',
                padding: '0.5rem',
                border: '1px solid #ccc',
                borderRadius: '4px',
                fontSize: '0.9rem'
              }}
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
              title="Przeglądaj katalogi"
            >
              📁 Przeglądaj
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
                title="Wyczyść katalog roboczy"
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
          className="cli-output-container"
          style={{
            fontFamily: '"Consolas", "Monaco", "Courier New", "Liberation Mono", "DejaVu Sans Mono", monospace',
            fontSize: '0.8rem',
            minHeight: '300px',
            maxHeight: '600px',
            overflowY: 'auto',
            overflowX: 'auto',
            marginBottom: '1rem',
            width: '100%',
            boxSizing: 'border-box',
            lineHeight: '1.4',
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
                // Dla tabel ASCII, renderuj jako ciągły tekst bez kolorowania
                const isTableOutput = item.text.includes('┏') || item.text.includes('┡') || 
                                     item.text.includes('┃') ||
                                     (item.text.includes('│') && item.text.length > 50);
                
                if (isTableOutput) {
                  // Dla tabel, renderuj jako element React z zachowaniem formatowania
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
                
                // Dla innych tekstów, użyj kolorowania
                const color = item.type === 'command' ? '#4ec9b0' : 
                             item.type === 'error' ? '#f48771' : 
                             item.type === 'info' ? '#ce9178' : '#d4d4d4';
                
                return <span key={idx} style={{ color }}>{item.text}</span>;
              })}
            </pre>
          )}
          {cliLoading && (
            <div style={{ color: '#888' }}>
              <span style={{ animation: 'blink 1s infinite' }}>▋</span> Wykonywanie...
            </div>
          )}
        </div>

        {/* CLI Input */}
        <div style={{ 
          display: 'flex', 
          gap: '0.5rem', 
          alignItems: 'center',
          width: '100%'
        }}>
          <span style={{ 
            color: '#4ec9b0', 
            fontFamily: 'Consolas, Monaco, "Courier New", monospace',
            fontWeight: 'bold',
            whiteSpace: 'nowrap',
            flexShrink: 0
          }}>
            {cliProgram} $
          </span>
          <input
            type="text"
            value={cliCommand}
            onChange={(e) => setCliCommand(e.target.value)}
            onKeyDown={handleCliKeyPress}
            placeholder="Type command..."
            disabled={cliLoading}
            style={{
              flex: 1,
              padding: '0.5rem',
              fontFamily: 'Consolas, Monaco, "Courier New", monospace',
              fontSize: '0.9rem',
              border: '1px solid #ccc',
              borderRadius: '4px',
              backgroundColor: cliLoading ? '#f5f5f5' : 'white',
              minWidth: 0  // Pozwala na kurczenie się
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
          Enter - wykonaj | ↑↓ - historia | Ctrl+L - wyczyść
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
              <h3 style={{ color: 'var(--text-primary)' }}>Wybierz katalog roboczy</h3>
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
                  ↑ Wstecz
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
                  placeholder="Wpisz ścieżkę..."
                />
                <button 
                  onClick={() => loadFileBrowser(browserPath)}
                  style={{ padding: '0.5rem 1rem' }}
                >
                  Przejdź
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
                    Katalog pusty
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
                          Wybierz
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

