import React, { useState, useEffect } from 'react'
import { deploymentAPI } from '../services/api'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import yaml from 'js-yaml'
import '../App.css'

function Deployments() {
  const [config, setConfig] = useState(null)
  const [configYaml, setConfigYaml] = useState('')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [strategy, setStrategy] = useState('rolling')

  useEffect(() => {
    loadConfig()
    loadHistory()
  }, [])

  const loadConfig = async () => {
    try {
      const response = await deploymentAPI.getConfig()
      const deploymentConfig = response.data.config
      setConfig(deploymentConfig)
      
      // Convert to YAML string for editing
      setConfigYaml(yaml.dump(deploymentConfig))
    } catch (error) {
      setMessage({ type: 'error', text: 'Error loading configuration' })
    }
  }

  const loadHistory = async () => {
    try {
      const response = await deploymentAPI.getHistory()
      setHistory(response.data.history || [])
    } catch (error) {
      console.error('Error loading history:', error)
    }
  }

  const handleConfigChange = (e) => {
    setConfigYaml(e.target.value)
  }

  const handleSaveConfig = async () => {
    try {
      const parsedConfig = yaml.load(configYaml)
      const response = await deploymentAPI.saveConfig(parsedConfig)
      if (response.data.success) {
        setMessage({ type: 'success', text: 'Configuration saved' })
        setConfig(parsedConfig)
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || error.message || 'Error saving configuration (check YAML syntax)' 
      })
    }
  }

  const handleExecute = async () => {
    if (!configYaml) {
      setMessage({ type: 'error', text: 'Configuration is empty' })
      return
    }

    setLoading(true)
    setMessage(null)

    try {
      const parsedConfig = yaml.load(configYaml)
      
      const response = await deploymentAPI.execute(parsedConfig, strategy)
      
      if (response.data.success) {
        setMessage({ type: 'success', text: 'Deployment completed successfully!' })
        loadHistory()
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error during deployment' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error during deployment' 
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h2>Deployment Management</h2>
      
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      <div className="card" style={{ marginBottom: '1rem' }}>
        <div className="btn-group">
          <button className="btn btn-secondary" onClick={loadConfig}>
            Load Configuration
          </button>
          <button className="btn btn-secondary" onClick={handleSaveConfig}>
            Save Configuration
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <label>Strategy:</label>
            <select 
              value={strategy} 
              onChange={(e) => setStrategy(e.target.value)}
              style={{ padding: '0.5rem' }}
            >
              <option value="rolling">Rolling</option>
              <option value="blue-green">Blue-Green</option>
              <option value="canary">Canary</option>
            </select>
            <button 
              className="btn btn-primary" 
              onClick={handleExecute}
              disabled={loading}
            >
              {loading ? 'Running...' : 'Run Deployment'}
            </button>
          </div>
        </div>
      </div>

      <div className="two-column">
        {/* Left: Config Editor */}
        <div className="card">
          <h3 className="card-title">Deployment Configuration</h3>
          <textarea
            value={configYaml}
            onChange={handleConfigChange}
            style={{
              width: '100%',
              minHeight: '500px',
              fontFamily: 'monospace',
              padding: '1rem',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              backgroundColor: '#1e1e1e',
              color: '#ffffff',
              fontSize: '0.9rem',
              lineHeight: '1.5'
            }}
          />
        </div>

        {/* Right: History */}
        <div className="card">
          <h3 className="card-title">Deployment History</h3>
          {history.length === 0 ? (
            <p style={{ color: '#666', textAlign: 'center', padding: '2rem' }}>
              No deployment history
            </p>
          ) : (
            <div style={{ maxHeight: '500px', overflowY: 'auto' }}>
              {history.slice().reverse().map((item, index) => (
                <div
                  key={index}
                  style={{
                    padding: '1rem',
                    marginBottom: '1rem',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    backgroundColor: item.status === 'success' ? '#d4edda' : '#f8d7da'
                  }}
                >
                  <div style={{ fontWeight: 'bold', marginBottom: '0.5rem' }}>
                    {new Date(item.timestamp).toLocaleString('en-US')}
                  </div>
                  <div style={{ marginBottom: '0.5rem' }}>
                    Strategy: <strong>{item.strategy}</strong>
                  </div>
                  <div>
                    Status: <strong>{item.status}</strong>
                  </div>
                  {item.output && (
                    <details style={{ marginTop: '0.5rem' }}>
                      <summary style={{ cursor: 'pointer' }}>Output</summary>
                      <pre style={{ 
                        marginTop: '0.5rem',
                        padding: '0.5rem',
                        backgroundColor: '#f5f5f5',
                        borderRadius: '4px',
                        overflow: 'auto',
                        fontSize: '0.85rem'
                      }}>
                        {item.output}
                      </pre>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default Deployments

