import React, { useState } from 'react'
import { useServer } from '../contexts/ServerContext'
import { useTheme } from '../contexts/ThemeContext'

function ServerSelector() {
  const { theme } = useTheme()
  const { servers, selectedServer, selectedServerInfo, loading, selectServer } = useServer()
  const [isOpen, setIsOpen] = useState(false)

  const handleServerChange = async (serverId) => {
    const result = await selectServer(serverId, false)
    if (result.success) {
      setIsOpen(false)
    }
  }

  const getServerDisplayName = (serverId) => {
    if (serverId === 'local') {
      return '🏠 Local'
    }
    const server = servers.find(s => s.id === serverId)
    return server ? `${server.name} (${server.hostname})` : serverId
  }

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={loading}
        style={{
          background: 'rgba(255, 255, 255, 0.2)',
          border: '1px solid rgba(255, 255, 255, 0.3)',
          borderRadius: '4px',
          padding: '0.5rem 1rem',
          cursor: loading ? 'not-allowed' : 'pointer',
          color: 'white',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontSize: '0.9rem',
          opacity: loading ? 0.6 : 1,
          transition: 'all 0.3s'
        }}
        title={selectedServerInfo ? `${selectedServerInfo.username}@${selectedServerInfo.hostname}:${selectedServerInfo.port}` : 'Select server'}
      >
        <span>🖥️</span>
        <span>{getServerDisplayName(selectedServer)}</span>
        <span>{isOpen ? '▲' : '▼'}</span>
      </button>

      {isOpen && (
        <>
          <div
            style={{
              position: 'fixed',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 1000
            }}
            onClick={() => setIsOpen(false)}
          />
          <div
            style={{
              position: 'absolute',
              top: '100%',
              right: 0,
              marginTop: '0.5rem',
              backgroundColor: theme === 'dark' ? 'var(--card-bg)' : 'white',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              minWidth: '250px',
              zIndex: 1001,
              maxHeight: '400px',
              overflowY: 'auto'
            }}
          >
            <div
              style={{
                padding: '0.5rem',
                borderBottom: '1px solid var(--border-color)',
                fontWeight: 'bold',
                color: 'var(--text-primary)',
                fontSize: '0.85rem'
              }}
            >
              Select server:
            </div>
            <div
              onClick={() => handleServerChange('local')}
              style={{
                padding: '0.75rem 1rem',
                cursor: 'pointer',
                backgroundColor: selectedServer === 'local' ? 'var(--bg-tertiary)' : 'transparent',
                color: 'var(--text-primary)',
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                borderBottom: '1px solid var(--border-color)'
              }}
              onMouseEnter={(e) => {
                if (selectedServer !== 'local') {
                  e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                }
              }}
              onMouseLeave={(e) => {
                if (selectedServer !== 'local') {
                  e.currentTarget.style.backgroundColor = 'transparent'
                }
              }}
            >
              <span>🏠</span>
              <span style={{ flex: 1 }}>Local</span>
              {selectedServer === 'local' && <span>✓</span>}
            </div>
            {servers.map((server) => (
              <div
                key={server.id}
                onClick={() => handleServerChange(server.id)}
                style={{
                  padding: '0.75rem 1rem',
                  cursor: 'pointer',
                  backgroundColor: selectedServer === server.id ? 'var(--bg-tertiary)' : 'transparent',
                  color: 'var(--text-primary)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  borderBottom: '1px solid var(--border-color)'
                }}
                onMouseEnter={(e) => {
                  if (selectedServer !== server.id) {
                    e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                  }
                }}
                onMouseLeave={(e) => {
                  if (selectedServer !== server.id) {
                    e.currentTarget.style.backgroundColor = 'transparent'
                  }
                }}
              >
                <span>🖥️</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: '600' }}>{server.name}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                    {server.username}@{server.hostname}:{server.port}
                  </div>
                </div>
                {selectedServer === server.id && <span>✓</span>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export default ServerSelector

