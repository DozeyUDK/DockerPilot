import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { serversAPI } from '../services/api'

const ServerContext = createContext()

export const useServer = () => {
  const context = useContext(ServerContext)
  if (!context) {
    throw new Error('useServer must be used within ServerProvider')
  }
  return context
}

export const ServerProvider = ({ children }) => {
  const [servers, setServers] = useState([])
  const [selectedServer, setSelectedServer] = useState('local')
  const [selectedServerInfo, setSelectedServerInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const lastLoadedServerRef = useRef(null) // Track last server we loaded info for

  // Load servers list
  const loadServers = useCallback(async () => {
    try {
      const response = await serversAPI.list()
      if (response.data.success) {
        setServers(response.data.servers || [])
      }
    } catch (error) {
      console.error('Error loading servers:', error)
    }
  }, [])

  // Load selected server
  const loadSelectedServer = useCallback(async () => {
    try {
      const response = await serversAPI.getSelected()
      if (response.data.success) {
        const serverId = response.data.server_id || 'local'
        setSelectedServer(serverId)
        
        if (serverId !== 'local' && response.data.server) {
          setSelectedServerInfo(response.data.server)
        } else {
          setSelectedServerInfo(null)
        }
      }
    } catch (error) {
      console.error('Error loading selected server:', error)
    }
  }, [])

  // Select server
  const selectServer = useCallback(async (serverId, setAsDefault = false) => {
    try {
      setLoading(true)
      const response = await serversAPI.select(serverId, setAsDefault)
      if (response.data.success) {
        setSelectedServer(serverId)
        
        if (serverId !== 'local') {
          // Find server info from list
          const server = servers.find(s => s.id === serverId)
          setSelectedServerInfo(server || null)
        } else {
          setSelectedServerInfo(null)
        }
        return { success: true }
      }
      return { success: false, error: response.data.error }
    } catch (error) {
      console.error('Error selecting server:', error)
      return { success: false, error: error.response?.data?.error || 'Error selecting server' }
    } finally {
      setLoading(false)
    }
  }, [servers])

  // Initial load
  useEffect(() => {
    loadServers()
    loadSelectedServer()
  }, [loadServers, loadSelectedServer])

  // Update server info when servers list changes and we have a selected server
  useEffect(() => {
    if (selectedServer !== 'local' && servers.length > 0) {
      const server = servers.find(s => s.id === selectedServer)
      if (server) {
        setSelectedServerInfo(server)
        lastLoadedServerRef.current = selectedServer
      } else if (lastLoadedServerRef.current !== selectedServer) {
        // Server not found in list, but we haven't tried loading for this server yet
        // This might happen if server was deleted elsewhere
        setSelectedServerInfo(null)
      }
    } else if (selectedServer === 'local') {
      setSelectedServerInfo(null)
      lastLoadedServerRef.current = null
    }
  }, [servers, selectedServer]) // This will only run when servers list actually changes, not on every render

  return (
    <ServerContext.Provider value={{
      servers,
      selectedServer,
      selectedServerInfo,
      loading,
      loadServers,
      loadSelectedServer,
      selectServer
    }}>
      {children}
    </ServerContext.Provider>
  )
}

