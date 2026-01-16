import React, { useState, useEffect, useRef } from 'react'
import { environmentAPI, statusAPI, fileBrowserAPI, serversAPI } from '../services/api'
import { useTheme } from '../contexts/ThemeContext'
import { useServer } from '../contexts/ServerContext'
import '../App.css'

function Environments() {
  const { theme } = useTheme()
  const { servers, selectedServer, loadServers, selectServer } = useServer()
  const [loading, setLoading] = useState({})
  const [message, setMessage] = useState(null)
  const [environmentsData, setEnvironmentsData] = useState(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [showContainerModal, setShowContainerModal] = useState(false)
  const [selectedEnv, setSelectedEnv] = useState(null)
  const [containers, setContainers] = useState([])
  const [loadingContainers, setLoadingContainers] = useState(false)
  const [containerSearch, setContainerSearch] = useState('')
  const [preparingConfig, setPreparingConfig] = useState(false)
  const [promotingContainer, setPromotingContainer] = useState(null)
  const [cancellingContainer, setCancellingContainer] = useState(null) // Track which container is being promoted
  const [configMode, setConfigMode] = useState('generate') // 'generate', 'select', or 'stage-single'
  const [browserPath, setBrowserPath] = useState('')
  const [browserItems, setBrowserItems] = useState([])
  const [loadingBrowser, setLoadingBrowser] = useState(false)
  const [fileSearch, setFileSearch] = useState('')
  const [importingConfig, setImportingConfig] = useState(false)
  const [selectedContainerForFile, setSelectedContainerForFile] = useState(null) // Container selected for file import
  const [showFileBrowser, setShowFileBrowser] = useState(false) // Show file browser after container selection
  const [showSudoModal, setShowSudoModal] = useState(false) // Show sudo password modal
  const [sudoPassword, setSudoPassword] = useState('') // Sudo password input
  const [sudoModalCallback, setSudoModalCallback] = useState(null) // Callback to execute after password is set (for useEffect tracking)
  const sudoModalCallbackRef = useRef(null) // Ref for callback (synchronous access)
  const [privilegedPaths, setPrivilegedPaths] = useState([]) // Paths requiring sudo
  const [sudoModalContainerName, setSudoModalContainerName] = useState(null) // Container name for modal
  const [deploymentProgress, setDeploymentProgress] = useState(null) // Progress tracking for deployment
  const cancelPollingRef = useRef(false) // Ref to cancel polling immediately
  const [showServerModal, setShowServerModal] = useState(false) // Show server add/edit modal
  const [editingServer, setEditingServer] = useState(null) // Server being edited (null for new)
  const [serverForm, setServerForm] = useState({
    name: '',
    hostname: '',
    port: 22,
    username: '',
    auth_type: 'password',
    password: '',
    private_key: '',
    key_passphrase: '',
    totp_secret: '',
    totp_code: '',
    description: ''
  })
  const [testingServer, setTestingServer] = useState(false) // Testing server connection
  const [testingServerResult, setTestingServerResult] = useState(null) // Test result
  const [showMigrateModal, setShowMigrateModal] = useState(false) // Show migrate container modal
  const [migratingContainer, setMigratingContainer] = useState(null) // Container being migrated
  const [migrationTargetServer, setMigrationTargetServer] = useState('') // Target server ID
  const [migrationIncludeData, setMigrationIncludeData] = useState(false) // Include volumes/data
  const [migrationStopSource, setMigrationStopSource] = useState(false) // Stop source container
  const [migrationProgress, setMigrationProgress] = useState(null) // Migration progress tracking
  const [migrationStarted, setMigrationStarted] = useState(false) // Migration polling guard
  const migrationProgressRef = useRef(null)
  const migratingContainerRef = useRef(null)

  useEffect(() => {
    migrationProgressRef.current = migrationProgress
  }, [migrationProgress])

  useEffect(() => {
    migratingContainerRef.current = migratingContainer
  }, [migratingContainer])

  const environments = [
    { name: 'dev', label: 'DEV', color: '#28a745' },
    { name: 'staging', label: 'Pre-Prod', color: '#ffc107' },
    { name: 'prod', label: 'PROD', color: '#dc3545' }
  ]

  useEffect(() => {
    loadEnvironmentsStatus()
    
    // Check for active deployments on page load/refresh
    const checkActiveDeployments = async () => {
      try {
        const response = await environmentAPI.getAllActiveDeployments()
        if (response.data.success && response.data.active_deployments) {
          const activeDeployments = response.data.active_deployments
          const containerNames = Object.keys(activeDeployments)
          
          // Filter out completed/failed/cancelled deployments
          const trulyActiveDeployments = {}
          for (const [name, progress] of Object.entries(activeDeployments)) {
            if (progress && progress.stage && 
                progress.stage !== 'completed' && 
                progress.stage !== 'failed' && 
                progress.stage !== 'error' && 
                progress.stage !== 'cancelled') {
              trulyActiveDeployments[name] = progress
            }
          }
          
          // If there are active deployments, set the first one as promoting
          // (in most cases there will be only one active deployment)
          const activeNames = Object.keys(trulyActiveDeployments)
          if (activeNames.length > 0) {
            const firstContainer = activeNames[0]
            setPromotingContainer(firstContainer)
            // Set initial progress
            if (trulyActiveDeployments[firstContainer]) {
              setDeploymentProgress(trulyActiveDeployments[firstContainer])
            }
            console.log(`[Environments] Found active deployment for ${firstContainer}, resuming progress tracking`)
          } else {
            // No active deployments, clear any stale progress
            if (promotingContainer && (!deploymentProgress || 
                deploymentProgress.stage === 'completed' || 
                deploymentProgress.stage === 'failed' || 
                deploymentProgress.stage === 'error' || 
                deploymentProgress.stage === 'cancelled')) {
              setPromotingContainer(null)
              setDeploymentProgress(null)
            }
          }
        } else {
          // No active deployments response, clear stale progress
          if (promotingContainer && (!deploymentProgress || 
              deploymentProgress.stage === 'completed' || 
              deploymentProgress.stage === 'failed' || 
              deploymentProgress.stage === 'error' || 
              deploymentProgress.stage === 'cancelled')) {
            setPromotingContainer(null)
            setDeploymentProgress(null)
          }
        }
      } catch (error) {
        console.error('Error checking active deployments:', error)
        // On error, don't clear progress - might be temporary network issue
      }
    }
    
    checkActiveDeployments()
  }, [selectedServer]) // Reload when server changes
  
  // Poll progress when promoting container
  useEffect(() => {
    if (!promotingContainer) {
      // Don't clear progress immediately - it might be restored from active deployments
      cancelPollingRef.current = false // Reset cancel flag
      return
    }
    
    // Reset cancel flag when starting new polling
    cancelPollingRef.current = false
    
    let intervalId = null
    
    const pollProgress = async () => {
      // Stop polling immediately if cancelled
      if (cancelPollingRef.current) {
        if (intervalId) {
          clearInterval(intervalId)
        }
        return
      }
      
      // Stop polling if promotingContainer was cleared
      if (!promotingContainer) {
        if (intervalId) {
          clearInterval(intervalId)
        }
        return
      }
      
      try {
        // Capture current container name to avoid stale closure
        const containerName = promotingContainer
        if (!containerName) {
          if (intervalId) {
            clearInterval(intervalId)
          }
          return
        }
        
        const response = await environmentAPI.getProgress(containerName)
        
        // Check again if cancelled or container changed (might have been cancelled during async call)
        if (cancelPollingRef.current || promotingContainer !== containerName) {
          if (intervalId) {
            clearInterval(intervalId)
          }
          return
        }
        
        if (response.data.success && response.data.progress) {
          const progress = response.data.progress
          setDeploymentProgress(progress)
          
          // Stop polling if deployment is completed, failed, or cancelled
          if (progress.stage === 'completed' || 
              progress.stage === 'failed' || 
              progress.stage === 'error' ||
              progress.stage === 'cancelled') {
            // Stop polling immediately
            if (intervalId) {
              clearInterval(intervalId)
              intervalId = null
            }
            // Wait a bit before clearing to show final message
            setTimeout(() => {
              // Double-check that this is still the same container (might have started new deployment)
              if (promotingContainer === containerName) {
                setDeploymentProgress(null)
                setPromotingContainer(null)
              }
            }, 3000) // 3 seconds to show final message (reduced from 5)
            return
          }
        } else {
          // If no progress found, the deployment might have completed and been cleaned up
          // Check if we should clear the progress
          if (!cancelPollingRef.current && promotingContainer === containerName) {
            // Wait a bit to see if progress comes back (might be temporary network issue)
            setTimeout(() => {
              // Check one more time if this is still the active container
              if (!cancelPollingRef.current && promotingContainer === containerName) {
                // Try to get progress one more time
                environmentAPI.getProgress(containerName).then(checkResponse => {
                  if (!checkResponse.data.success || !checkResponse.data.progress) {
                    // No progress found, clear it only if still the same container
                    if (promotingContainer === containerName) {
                      setDeploymentProgress(null)
                      setPromotingContainer(null)
                    }
                  }
                }).catch(() => {
                  // On error, assume deployment is done (only if still same container)
                  if (promotingContainer === containerName) {
                    setDeploymentProgress(null)
                    setPromotingContainer(null)
                  }
                })
              }
            }, 2000) // Wait 2 seconds before clearing (reduced from 3)
          }
        }
      } catch (error) {
        console.error('Error polling progress:', error)
        // On error, don't clear immediately - might be temporary network issue
        // But stop polling if cancelled
        if (cancelPollingRef.current && intervalId) {
          clearInterval(intervalId)
        }
      }
    }
    
    // Poll immediately and then every 1 second
    pollProgress()
    intervalId = setInterval(pollProgress, 1000)
    
    return () => {
      cancelPollingRef.current = true // Mark as cancelled when component unmounts or promotingContainer changes
      if (intervalId) {
        clearInterval(intervalId)
      }
    }
  }, [promotingContainer])
  
  // Poll migration progress when migrating container
  // Only start polling when migration is actually in progress (has progress or loading flag)
  useEffect(() => {
    if (!migratingContainer) {
      return
    }
    
    // Don't start polling until migration actually starts
    if (!migrationStarted) {
      return
    }
    
    let intervalId = null
    let consecutiveNoProgress = 0
    const MAX_CONSECUTIVE_NO_PROGRESS = 10 // Increased from 3 to allow more time for migration to start
    
    const pollMigrationProgress = async () => {
      if (!migratingContainerRef.current) {
        if (intervalId) {
          clearInterval(intervalId)
        }
        return
      }
      
      // Capture current container name to avoid stale closure
      const containerName = migratingContainerRef.current
      
      try {
        const response = await statusAPI.getMigrationProgress(containerName)
        
        // Check if container changed (might have been cleared during async call)
        if (migratingContainerRef.current !== containerName) {
          if (intervalId) {
            clearInterval(intervalId)
          }
          return
        }
        
        if (response.data.success && response.data.progress) {
          const progress = response.data.progress
          setMigrationProgress(progress)
          consecutiveNoProgress = 0 // Reset counter on successful progress
          // Clear loading flag when we get progress updates
          setLoading(prev => ({ ...prev, [`migrate_${containerName}`]: false }))
          
          // Stop polling if migration is completed, failed, or cancelled
          if (progress.stage === 'completed' || 
              progress.stage === 'failed' || 
              progress.stage === 'error' ||
              progress.stage === 'cancelled') {
            // Stop polling immediately
            if (intervalId) {
              clearInterval(intervalId)
              intervalId = null
            }
            // Wait a bit before clearing to show final message
            setTimeout(() => {
              // Double-check that this is still the same container (might have started new migration)
              if (migratingContainerRef.current === containerName) {
                setMigrationProgress(null)
                setMigratingContainer(null)
                setShowMigrateModal(false)
                setMigrationStarted(false)
                loadEnvironmentsStatus()
              }
            }, 3000) // 3 seconds to show final message (reduced from 5)
            return
          }
        } else {
          // No progress found - but only close modal if migration was actually started
          // (i.e., we had progress before but now it's gone, meaning it completed and was cleaned up)
          if (migrationProgressRef.current !== null) {
            // We had progress before, so migration was running - might have completed
            consecutiveNoProgress++
            if (consecutiveNoProgress >= MAX_CONSECUTIVE_NO_PROGRESS) {
              // Stop polling if no progress found after several attempts
              if (intervalId) {
                clearInterval(intervalId)
                intervalId = null
              }
              // Migration likely completed and was cleaned up - close modal
              setMigrationProgress(null)
              setMigratingContainer(null)
              setShowMigrateModal(false)
              setMigrationStarted(false)
              loadEnvironmentsStatus()
              return
            }
          }
          // If no progress and migration hasn't started yet, don't close modal
          // Just reset counter to allow more time
          consecutiveNoProgress = 0
        }
      } catch (error) {
        console.error('Error polling migration progress:', error)
        // Only count errors if migration was actually started
        if (migrationProgressRef.current !== null) {
          consecutiveNoProgress++
          // Stop polling after too many consecutive errors
          if (consecutiveNoProgress >= MAX_CONSECUTIVE_NO_PROGRESS) {
            if (intervalId) {
              clearInterval(intervalId)
              intervalId = null
            }
            setMessage({ 
              type: 'error', 
              text: 'Error checking migration progress. Check backend logs.' 
            })
            setMigrationProgress(null)
            setMigratingContainer(null)
            setShowMigrateModal(false)
            setMigrationStarted(false)
          }
        }
      }
    }
    
    // Poll immediately and then every 2 seconds
    pollMigrationProgress()
    intervalId = setInterval(pollMigrationProgress, 2000)
    
    return () => {
      if (intervalId) {
        clearInterval(intervalId)
      }
    }
  }, [migratingContainer, migrationStarted])
  
  // Debug: Monitor showSudoModal changes
  useEffect(() => {
    console.log('[useEffect] showSudoModal changed to:', showSudoModal)
    if (showSudoModal) {
      console.log('[useEffect] Modal should be visible! privilegedPaths:', privilegedPaths)
      console.log('[useEffect] sudoModalContainerName:', sudoModalContainerName)
      console.log('[useEffect] sudoModalCallback exists:', !!sudoModalCallback)
      console.log('[useEffect] sudoModalCallback type:', typeof sudoModalCallback)
      // Check if modal element exists in DOM
      setTimeout(() => {
        const modalElement = document.getElementById('sudo-password-modal')
        console.log('[useEffect] Modal element in DOM:', !!modalElement)
        if (modalElement) {
          console.log('[useEffect] Modal element styles:', window.getComputedStyle(modalElement))
        }
      }, 100)
    }
  }, [showSudoModal, privilegedPaths, sudoModalContainerName, sudoModalCallback])
  
  // Debug: Monitor sudoModalCallback changes
  useEffect(() => {
    console.log('[useEffect] sudoModalCallback changed:', {
      exists: !!sudoModalCallback,
      type: typeof sudoModalCallback
    })
  }, [sudoModalCallback])
  
  // Debug: Log when showSudoModal changes
  useEffect(() => {
    if (showSudoModal) {
      console.log('[useEffect] showSudoModal changed to TRUE - modal should be visible')
    } else {
      console.log('[useEffect] showSudoModal changed to FALSE - modal should be hidden')
    }
  }, [showSudoModal])
  
  // Debug: Monitor showSudoModal changes
  useEffect(() => {
    console.log('[useEffect] showSudoModal changed to:', showSudoModal)
    if (showSudoModal) {
      console.log('[useEffect] Modal should be visible! privilegedPaths:', privilegedPaths)
      console.log('[useEffect] sudoModalContainerName:', sudoModalContainerName)
    }
  }, [showSudoModal, privilegedPaths, sudoModalContainerName])
  
  // Debug: Log when showSudoModal changes
  useEffect(() => {
    console.log('[useEffect] showSudoModal changed to:', showSudoModal)
  }, [showSudoModal])

  const loadEnvironmentsStatus = async () => {
    setLoadingStatus(true)
    try {
      const response = await environmentAPI.getStatus()
      if (response.data.success) {
        setEnvironmentsData(response.data.environments)
      }
    } catch (error) {
      console.error('Error loading environments status:', error)
    } finally {
      setLoadingStatus(false)
    }
  }


  const handleServerCreate = async () => {
    try {
      const response = await serversAPI.create(serverForm)
      if (response.data.success) {
        setMessage({ type: 'success', text: response.data.message || 'Server created successfully' })
        setShowServerModal(false)
        resetServerForm()
        loadServers() // Refresh servers from context
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error creating server' })
      }
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.error || 'Error creating server' })
    }
  }

  const handleServerUpdate = async () => {
    if (!editingServer) return
    
    try {
      const response = await serversAPI.update(editingServer.id, serverForm)
      if (response.data.success) {
        setMessage({ type: 'success', text: response.data.message || 'Server updated successfully' })
        setShowServerModal(false)
        setEditingServer(null)
        resetServerForm()
        loadServers() // Refresh servers from context
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error updating server' })
      }
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.error || 'Error updating server' })
    }
  }

  const handleServerDelete = async (serverId) => {
    if (!window.confirm('Are you sure you want to delete this server?')) {
      return
    }
    
    try {
      const response = await serversAPI.delete(serverId)
      if (response.data.success) {
        setMessage({ type: 'success', text: 'Server deleted successfully' })
        if (selectedServer === serverId) {
          selectServer('local') // Use context function
        }
        loadServers() // Refresh servers from context
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error deleting server' })
      }
    } catch (error) {
      setMessage({ type: 'error', text: error.response?.data?.error || 'Error deleting server' })
    }
  }

  const handleServerTest = async (serverId = null, testData = null) => {
    setTestingServer(true)
    setTestingServerResult(null)
    
    try {
      const response = await serversAPI.test(serverId, testData || serverForm)
      setTestingServerResult(response.data)
      if (response.data.success) {
        setMessage({ type: 'success', text: response.data.message || 'Server connection successful' })
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Server connection error' })
      }
    } catch (error) {
      setTestingServerResult({ success: false, error: error.response?.data?.error || 'Error testing connection' })
      setMessage({ type: 'error', text: error.response?.data?.error || 'Error testing connection' })
    } finally {
      setTestingServer(false)
    }
  }

  const openServerModal = (server = null) => {
    if (server) {
      setEditingServer(server)
      setServerForm({
        name: server.name || '',
        hostname: server.hostname || '',
        port: server.port || 22,
        username: server.username || '',
        auth_type: server.auth_type || 'password',
        password: '', // Don't show existing password
        private_key: '', // Don't show existing key
        key_passphrase: '',
        totp_secret: '',
        description: server.description || ''
      })
    } else {
      resetServerForm()
    }
    setShowServerModal(true)
    setTestingServerResult(null)
  }

  const resetServerForm = () => {
    setEditingServer(null)
    setServerForm({
      name: '',
      hostname: '',
      port: 22,
      username: '',
      auth_type: 'password',
      password: '',
      private_key: '',
      key_passphrase: '',
      totp_secret: '',
      totp_code: '',
      description: ''
    })
  }

  const handleKeyFileUpload = (event) => {
    const file = event.target.files[0]
    if (file) {
      const reader = new FileReader()
      reader.onload = (e) => {
        setServerForm({ ...serverForm, private_key: e.target.result })
      }
      reader.readAsText(file)
    }
  }

  const handlePromote = async (fromEnv, toEnv) => {
    if (!window.confirm(`Are you sure you want to promote from ${fromEnv.toUpperCase()} to ${toEnv.toUpperCase()}?`)) {
      return
    }

    const key = `${fromEnv}-${toEnv}`
    setLoading({ ...loading, [key]: true })
    setMessage(null)

    try {
      const response = await environmentAPI.promote(fromEnv, toEnv)
      if (response.data.success) {
        setMessage({ 
          type: 'success', 
          text: `Promotion from ${fromEnv.toUpperCase()} to ${toEnv.toUpperCase()} completed successfully!` 
        })
        // Reload status after successful promotion
        await loadEnvironmentsStatus()
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error during promotion' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error promoting environment' 
      })
    } finally {
      setLoading({ ...loading, [key]: false })
    }
  }

  const getEnvironmentData = (envName) => {
    if (!environmentsData) return null
    return environmentsData[envName] || null
  }

  const getStatusLabel = (status) => {
    const labels = {
      'active': 'Aktywne',
      'inactive': 'Nieaktywne',
      'empty': 'Puste'
    }
    return labels[status] || 'Nieznany'
  }

  const getStatusColor = (status) => {
    const colors = {
      'active': '#28a745',
      'inactive': '#6c757d',
      'empty': '#ffc107'
    }
    return colors[status] || '#6c757d'
  }

  const handleEnvClick = async (envName) => {
    setSelectedEnv(envName)
    setShowContainerModal(true)
    setConfigMode('generate')
    setContainerSearch('')
    setLoadingContainers(true)
    
    try {
      // Use existing environmentsData if available, otherwise fetch fresh data
      let envData = null
      if (environmentsData && environmentsData[envName]) {
        envData = environmentsData[envName]
      } else {
        // Reload environment status to get fresh data
        const response = await environmentAPI.getStatus()
        if (response.data.success && response.data.environments) {
          setEnvironmentsData(response.data.environments)
          envData = response.data.environments[envName]
        }
      }
      
      if (envData && envData.containers) {
        // Get containers from environment data (already filtered by environment)
        // Use full list if available, otherwise use limited list
        const envContainers = envData.containers.all || envData.containers.list || []
        // Filter only running containers for the modal
        const runningContainers = envContainers.filter(c => c.state === 'running')
        setContainers(runningContainers)
      } else {
        // No containers in this environment
        setContainers([])
      }
    } catch (error) {
      console.error('Error loading containers:', error)
      setMessage({ type: 'error', text: 'Error loading containers for environment ' + envName.toUpperCase() })
      setContainers([])
    } finally {
      setLoadingContainers(false)
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
      setMessage({ type: 'error', text: 'Error loading file browser' })
    } finally {
      setLoadingBrowser(false)
    }
  }

  const handleImportConfig = async (filePath, containerName = null) => {
    if (!selectedEnv) return
    
    // Use selected container name or extract from file path
    const targetContainerName = containerName || selectedContainerForFile
    
    if (!targetContainerName) {
      setMessage({ type: 'error', text: 'Select a container before importing a file' })
      return
    }
    
    setImportingConfig(true)
    try {
      const response = await environmentAPI.importConfig(filePath, selectedEnv, targetContainerName)
      if (response.data.success) {
        setMessage({ 
          type: 'success', 
          text: `Configuration from ${filePath.split('/').pop()} imported for container ${targetContainerName} in ${selectedEnv.toUpperCase()}` 
        })
        setShowContainerModal(false)
        setShowFileBrowser(false)
        setSelectedContainerForFile(null)
        await loadEnvironmentsStatus()
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error importing configuration' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error importing configuration' 
      })
    } finally {
      setImportingConfig(false)
    }
  }

  const handleSelectContainerForFile = (containerName) => {
    setSelectedContainerForFile(containerName)
    setShowFileBrowser(true)
    setBrowserPath('/home/dozey/DockerPilot')
    loadFileBrowser('/home/dozey/DockerPilot')
  }

  const handlePrepareConfig = async (containerName) => {
    if (!selectedEnv) return
    
    setPreparingConfig(true)
    try {
      const response = await environmentAPI.prepareConfig(containerName, selectedEnv)
      if (response.data.success) {
        setMessage({ 
          type: 'success', 
          text: `Configuration for ${containerName} prepared for ${selectedEnv.toUpperCase()}` 
        })
        setShowContainerModal(false)
        await loadEnvironmentsStatus()
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error preparing configuration' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error preparing configuration' 
      })
    } finally {
      setPreparingConfig(false)
    }
  }

  const handleMigrateContainer = async (containerName) => {
    setMigratingContainer(containerName)
    setShowMigrateModal(true)
    setMigrationTargetServer('')
    setMigrationIncludeData(false)
    setMigrationStopSource(false)
    setMigrationStarted(false)
  }

  const executeMigration = async () => {
    if (!migratingContainer || !migrationTargetServer) {
      setMessage({ type: 'error', text: 'Select a container and target server' })
      return
    }

    if (migrationTargetServer === selectedServer) {
      setMessage({ type: 'error', text: 'Target server must be different than the source' })
      return
    }

    setLoading({ ...loading, [`migrate_${migratingContainer}`]: true })
    setMessage(null)
    // Don't set initial progress here - let backend set it and polling will pick it up
    // setMigrationProgress will be updated by polling useEffect

    try {
      // Start migration (non-blocking, progress will be polled)
      const response = await statusAPI.migrateContainer(
        migratingContainer,
        selectedServer || 'local',
        migrationTargetServer,
        migrationIncludeData,
        migrationStopSource
      )

      if (!response.data.success) {
        setMessage({ type: 'error', text: response.data.error || 'Error during migration' })
        setLoading(prev => ({ ...prev, [`migrate_${migratingContainer}`]: false }))
        setMigrationProgress(null)
        setMigrationStarted(false)
        // Don't close modal on error - let user see the error message
        // setMigratingContainer(null)
      } else {
        // Migration started successfully - polling will pick up progress
        // Keep loading flag set so polling knows migration has started
        setMigrationStarted(true)
      }
      // Progress will be updated via polling - don't clear loading here, let polling handle it
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error migrating container' 
      })
      setLoading({ ...loading, [`migrate_${migratingContainer}`]: false })
      setMigrationProgress(null)
      setMigratingContainer(null)
      setMigrationStarted(false)
    }
  }

  const handleCancelMigration = async () => {
    if (!migratingContainer) return
    
    if (!window.confirm(`Are you sure you want to cancel migration of container ${migratingContainer}?`)) {
      return
    }
    
    try {
      const response = await statusAPI.cancelMigration(migratingContainer)
      if (response.data.success) {
        setMessage({ type: 'warning', text: 'Container migration cancelled' })
        // Progress will be updated via polling to show cancellation
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error cancelling migration' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error cancelling migration' 
      })
    }
  }

  const handleStopPromotion = async (containerName) => {
    if (!window.confirm(`Are you sure you want to STOP promotion of container ${containerName}?\n\nDeployment will be cancelled.`)) {
      return
    }
    
    setCancellingContainer(containerName)
    try {
      const response = await environmentAPI.cancelPromotion(containerName)
      if (response.data.success) {
        setMessage({ type: 'warning', text: `Container promotion cancelled: ${containerName}` })
        
        // Stop polling immediately by setting cancel flag
        cancelPollingRef.current = true
        
        // Update progress to show cancellation immediately
        setDeploymentProgress({
          stage: 'cancelled',
          progress: deploymentProgress?.progress || 0,
          message: `Container promotion cancelled: ${containerName}`,
          timestamp: new Date().toISOString()
        })
        
        // Clear promotingContainer to stop polling
        setPromotingContainer(null)
        
        // Clear progress after showing cancellation message
        setTimeout(() => {
          setDeploymentProgress(null)
        }, 5000)
      } else {
        setMessage({ type: 'error', text: response.data.error || 'Error cancelling' })
      }
    } catch (error) {
      setMessage({ 
        type: 'error', 
        text: error.response?.data?.error || 'Error cancelling promotion' 
      })
    } finally {
      setCancellingContainer(null)
    }
  }

  const handleStageSingleContainer = async (containerName) => {
    console.log(`[handleStageSingleContainer] Called for ${containerName}`)
    // Determine target environment based on current environment
    const envFlow = {
      'dev': 'staging',
      'staging': 'prod',
      'prod': null // Can't promote from prod
    }
    
    const envLabels = {
      'dev': 'DEV',
      'staging': 'Pre-Prod',
      'prod': 'PROD'
    }
    
    const targetEnv = envFlow[selectedEnv]
    if (!targetEnv) {
      setMessage({ type: 'error', text: 'Cannot promote from PROD (highest environment)' })
      return
    }
    
    const sourceLabel = envLabels[selectedEnv] || selectedEnv.toUpperCase()
    const targetLabel = envLabels[targetEnv] || targetEnv.toUpperCase()
    
    // Check if sudo will be required for backup and get mount information
    let skipBackup = false
    try {
      const sudoCheck = await environmentAPI.checkSudo(containerName)
      console.log(`Sudo check for ${containerName}:`, sudoCheck.data)
      
      // Always show modal if backup is needed (either requires sudo OR has large mounts)
      const requiresSudo = sudoCheck.data && sudoCheck.data.requires_sudo
      const hasLargeMounts = sudoCheck.data && sudoCheck.data.has_large_mounts
      const shouldShowModal = requiresSudo || hasLargeMounts
      
      if (shouldShowModal) {
        console.log(`[handleStageSingleContainer] Showing backup modal for ${containerName}`, {
          requiresSudo,
          hasLargeMounts,
          totalSizeTB: sudoCheck.data?.total_size_tb,
          largeMounts: sudoCheck.data?.large_mounts
        })
        
        // Show backup confirmation modal - set all states synchronously
        console.log(`[handleStageSingleContainer] Setting modal state synchronously...`)
        setPrivilegedPaths(sudoCheck.data.privileged_paths || [])
        setSudoModalContainerName(containerName)
        
        // Store mount info for display in modal
        setSudoPassword(JSON.stringify({
          requires_sudo: requiresSudo,
          has_large_mounts: hasLargeMounts,
          total_size_tb: sudoCheck.data?.total_size_tb || 0,
          total_size_gb: sudoCheck.data?.total_size_gb || 0,
          large_mounts: sudoCheck.data?.large_mounts || [],
          warning: sudoCheck.data?.warning
        }))
        
        // Force state update
        console.log(`[handleStageSingleContainer] About to set showSudoModal to true`)
        setShowSudoModal(true)
        console.log(`[handleStageSingleContainer] showSudoModal set to true`)
        
        // Verify state was set after a tick
        setTimeout(() => {
          console.log(`[handleStageSingleContainer] After setTimeout, checking showSudoModal state...`)
          const modalElement = document.getElementById('sudo-password-modal')
          if (modalElement) {
            console.log(`[handleStageSingleContainer] ✅ Modal found in DOM after setTimeout!`)
          } else {
            console.error(`[handleStageSingleContainer] ❌ Modal NOT found in DOM after setTimeout!`)
            // Force a re-render by toggling state
            setShowSudoModal(false)
            setTimeout(() => {
              console.log(`[handleStageSingleContainer] Toggling showSudoModal back to true`)
              setShowSudoModal(true)
            }, 10)
          }
        }, 100)
        
        console.log(`[handleStageSingleContainer] All modal states set, showSudoModal should be true now`)
        
        // Verify modal appears in DOM after React re-render
        // Use double requestAnimationFrame to ensure React has rendered
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            setTimeout(() => {
              const modal = document.getElementById('sudo-password-modal')
              console.log(`[handleStageSingleContainer] Checking for modal in DOM...`)
              if (modal) {
                console.log(`[handleStageSingleContainer] ✅ Modal found in DOM!`, modal)
                console.log(`[handleStageSingleContainer] Modal styles:`, window.getComputedStyle(modal))
                // Force focus on input
                const input = modal.querySelector('input[type="password"]')
                if (input) {
                  input.focus()
                  console.log(`[handleStageSingleContainer] Input focused`)
                }
              } else {
                console.error(`[handleStageSingleContainer] ❌ Modal NOT found in DOM!`)
                // Check if showSudoModal state is actually true
                console.error(`[handleStageSingleContainer] Current showSudoModal value in closure may be stale`)
              }
            }, 200)
          })
        })
        
        // Set callback for when user provides password
        // Capture variables in closure to ensure they're available when callback is called
        const capturedContainerName = containerName
        const capturedSelectedEnv = selectedEnv
        const capturedTargetEnv = targetEnv
        const capturedSourceLabel = sourceLabel
        const capturedTargetLabel = targetLabel
        
        console.log(`[handleStageSingleContainer] Creating callback with captured variables:`, {
          containerName: capturedContainerName,
          selectedEnv: capturedSelectedEnv,
          targetEnv: capturedTargetEnv
        })
        
        const callbackFunction = async (password, shouldSkip) => {
          console.log(`[handleStageSingleContainer] Backup modal callback called`, { 
            hasPassword: !!password, 
            shouldSkip,
            containerName: capturedContainerName,
            selectedEnv: capturedSelectedEnv,
            targetEnv: capturedTargetEnv
          })
          
          try {
            setShowSudoModal(false)
            
            // Parse mount info from sudoPassword (we stored it there temporarily)
            let mountInfo = null
            try {
              mountInfo = JSON.parse(sudoPassword || '{}')
            } catch {
              mountInfo = {}
            }
            
            setSudoPassword('')
            
            let actualSkipBackup = false
            
            if (shouldSkip) {
              const confirmSkip = window.confirm(
                `⚠️ Are you sure you want to SKIP the backup?\n\n` +
                `This means the container data WILL NOT be protected before promotion!\n\n` +
                `Promote ${capturedContainerName} from ${capturedSourceLabel} to ${capturedTargetLabel} WITHOUT backup?`
              )
              if (!confirmSkip) {
                console.log(`[handleStageSingleContainer] User cancelled skip backup`)
                sudoModalCallbackRef.current = null
                setSudoModalCallback(null)
                return
              }
              actualSkipBackup = true
            } else if (password && mountInfo.requires_sudo) {
              console.log(`[handleStageSingleContainer] Storing sudo password...`)
              // Store sudo password only if sudo is required
              try {
                const response = await environmentAPI.setSudoPassword(password)
                console.log(`[handleStageSingleContainer] Sudo password stored:`, response.data)
              } catch (error) {
                console.error(`[handleStageSingleContainer] Error storing sudo password:`, error)
                setMessage({ type: 'error', text: 'Error saving sudo password' })
                sudoModalCallbackRef.current = null
                setSudoModalCallback(null)
                return
              }
            } else if (!password && mountInfo.requires_sudo) {
              // User cancelled but sudo was required
              console.log(`[handleStageSingleContainer] User cancelled (sudo required but no password)`)
              setSudoModalCallback(null)
              return
            }
            // If no sudo required and no password, just continue (user clicked Continue without password)
            
            // Clear callback before continuing
            setSudoModalCallback(null)
            
            // Continue with promotion
            console.log(`[handleStageSingleContainer] Continuing with promotion...`, {
              containerName: capturedContainerName,
              selectedEnv: capturedSelectedEnv,
              targetEnv: capturedTargetEnv,
              skipBackup: actualSkipBackup
            })
            await continuePromotion(capturedContainerName, capturedSelectedEnv, capturedTargetEnv, capturedSourceLabel, capturedTargetLabel, actualSkipBackup)
          } catch (error) {
            console.error(`[handleStageSingleContainer] Error in callback:`, error)
            setMessage({ type: 'error', text: 'Processing error: ' + (error.message || 'Unknown error') })
            setSudoModalCallback(null)
          }
        }
        
        console.log(`[handleStageSingleContainer] Setting sudoModalCallback...`)
        // Set both state (for useEffect) and ref (for synchronous access)
        sudoModalCallbackRef.current = callbackFunction
        setSudoModalCallback(callbackFunction)
        console.log(`[handleStageSingleContainer] sudoModalCallback set in both state and ref`)
        
        // Verify callback was set
        setTimeout(() => {
          console.log(`[handleStageSingleContainer] Verifying callback after state update...`)
          // Note: We can't directly check state here, but we can log
        }, 100)
        
        return // Wait for user to provide password or skip
      }
    } catch (error) {
      console.warn('Could not check sudo requirements:', error)
      // Continue anyway - sudo check is not critical
    }
    
    // No sudo required - continue directly
    console.log(`No sudo required for ${containerName}, continuing with promotion`)
    await continuePromotion(containerName, selectedEnv, targetEnv, sourceLabel, targetLabel, skipBackup)
  }
  
  const continuePromotion = async (containerName, selectedEnv, targetEnv, sourceLabel, targetLabel, skipBackup) => {
    console.log(`[continuePromotion] Called for ${containerName}`, { selectedEnv, targetEnv, skipBackup })
    
    // Final confirmation
    if (!skipBackup) {
      const confirmed = window.confirm(`Are you sure you want to promote container ${containerName} from ${sourceLabel} to ${targetLabel}?`)
      console.log(`[continuePromotion] User confirmed: ${confirmed}`)
      if (!confirmed) {
        console.log(`[continuePromotion] User cancelled, returning`)
        return
      }
    }
    
    setPromotingContainer(containerName)
    try {
      console.log(`[continuePromotion] Preparing config for ${containerName} to ${targetEnv}`)
      // First, prepare config for target environment
      const prepareResponse = await environmentAPI.prepareConfig(containerName, targetEnv)
      console.log(`[continuePromotion] Prepare config response:`, prepareResponse.data)
      
      if (!prepareResponse.data.success) {
        const errorMsg = prepareResponse.data.error || 'Error preparing configuration'
        console.error(`[continuePromotion] Prepare config failed: ${errorMsg}`)
        setMessage({ type: 'error', text: errorMsg })
        setPromotingContainer(null)
        return
      }
      
      // Then promote to target environment (with optional skipBackup flag)
      console.log(`Promoting ${containerName} from ${selectedEnv} to ${targetEnv}`)
      const promoteResponse = await environmentAPI.promoteSingle(selectedEnv, targetEnv, containerName, skipBackup)
      console.log(`Promote response:`, promoteResponse.data)
      
      // Clear sudo password after promotion (for security)
      try {
        await environmentAPI.clearSudoPassword()
      } catch (error) {
        // Ignore errors when clearing password
      }
      
      if (promoteResponse.data.success) {
        const backupNote = skipBackup ? ' (without backup)' : ''
        setMessage({ 
          type: 'success', 
          text: `Container ${containerName} promoted from ${sourceLabel} to ${targetLabel}${backupNote}!` 
        })
        setShowContainerModal(false)
        await loadEnvironmentsStatus()
      } else {
        const errorMsg = promoteResponse.data.error || 'Error during promotion'
        console.error(`Promotion failed: ${errorMsg}`)
        setMessage({ type: 'error', text: errorMsg })
      }
    } catch (error) {
      console.error(`Promotion error for ${containerName}:`, error)
      const errorMsg = error.response?.data?.error || 'Error promoting container'
      setMessage({ 
        type: 'error', 
        text: errorMsg
      })
    } finally {
      setPromotingContainer(null)
    }
  }

  const filteredContainers = containers.filter(container => 
    container.name.toLowerCase().includes(containerSearch.toLowerCase()) ||
    (container.image && container.image.toLowerCase().includes(containerSearch.toLowerCase()))
  )

  const filteredFiles = browserItems.filter(item => {
    if (item.is_dir) return true
    const fileName = item.name.toLowerCase()
    return fileName.includes('deployment') && (fileName.endsWith('.yml') || fileName.endsWith('.yaml')) &&
           (fileSearch === '' || fileName.includes(fileSearch.toLowerCase()) || item.path.toLowerCase().includes(fileSearch.toLowerCase()))
  })

  return (
    <div>
      <style>
        {`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
          }
        `}
      </style>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
        <h2 style={{ margin: 0 }}>Environment Promotion</h2>
        <button
          onClick={() => loadEnvironmentsStatus()}
          disabled={loadingStatus}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: loadingStatus ? '#6c757d' : '#007bff',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: loadingStatus ? 'not-allowed' : 'pointer',
            fontWeight: '600',
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem'
          }}
          title="Refresh environment data"
        >
          {loadingStatus ? '⏳ Refreshing...' : '🔄 Refresh'}
        </button>
      </div>
      
      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
        </div>
      )}

      {/* Server Management Card */}
      <div className="card" style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 className="card-title" style={{ margin: 0 }}>Server Management</h3>
          <button
            onClick={() => openServerModal()}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: '600'
            }}
          >
            + Add Server
          </button>
        </div>
        
        <div style={{ 
          padding: '0.75rem', 
          backgroundColor: 'var(--bg-tertiary)', 
          borderRadius: '4px',
          marginBottom: '1rem',
          fontSize: '0.9rem',
          color: 'var(--text-secondary)'
        }}>
          <strong>Tip:</strong> Server selection can be changed in the navigation menu (top right corner). Currently selected server: <strong>{selectedServer === 'local' ? '🏠 Local' : servers.find(s => s.id === selectedServer)?.name || selectedServer}</strong>
        </div>

        {/* Server List */}
        {servers.length > 0 && (
          <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border-color)' }}>
            <h4 style={{ marginBottom: '0.5rem', color: 'var(--text-primary)' }}>Configured servers:</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {servers.map((server) => (
                <div
                  key={server.id}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '0.75rem',
                    backgroundColor: selectedServer === server.id ? 'var(--bg-tertiary)' : 'transparent',
                    border: `1px solid ${selectedServer === server.id ? '#007bff' : 'var(--border-color)'}`,
                    borderRadius: '4px'
                  }}
                >
                  <div>
                    <div style={{ fontWeight: '600', color: 'var(--text-primary)' }}>
                      {server.name}
                      {selectedServer === server.id && <span style={{ marginLeft: '0.5rem', color: '#007bff' }}>✓</span>}
                    </div>
                    <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      {server.username}@{server.hostname}:{server.port} ({server.auth_type})
                    </div>
                    {server.description && (
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                        {server.description}
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      onClick={() => handleServerTest(server.id)}
                      disabled={testingServer}
                      style={{
                        padding: '0.25rem 0.75rem',
                        backgroundColor: '#28a745',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: testingServer ? 'not-allowed' : 'pointer',
                        fontSize: '0.85rem'
                      }}
                    >
                      {testingServer ? 'Testing...' : 'Test'}
                    </button>
                    <button
                      onClick={() => openServerModal(server)}
                      style={{
                        padding: '0.25rem 0.75rem',
                        backgroundColor: '#ffc107',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '0.85rem'
                      }}
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleServerDelete(server.id)}
                      style={{
                        padding: '0.25rem 0.75rem',
                        backgroundColor: '#dc3545',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '0.85rem'
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {testingServerResult && (
          <div style={{
            marginTop: '1rem',
            padding: '0.75rem',
            backgroundColor: testingServerResult.success ? 'rgba(40, 167, 69, 0.1)' : 'rgba(220, 53, 69, 0.1)',
            border: `1px solid ${testingServerResult.success ? '#28a745' : '#dc3545'}`,
            borderRadius: '4px',
            color: testingServerResult.success ? '#28a745' : '#dc3545'
          }}>
            {testingServerResult.success ? '✓ ' : '✗ '}
            {testingServerResult.message || testingServerResult.error || 'Test result'}
          </div>
        )}
      </div>
      
      {deploymentProgress && (
        <div className="card" style={{ 
          marginBottom: '20px', 
          backgroundColor: theme === 'dark' ? 'var(--card-bg)' : '#f8f9fa',
          color: 'var(--text-primary)'
        }}>
          <h4 style={{ marginTop: 0, color: 'var(--text-primary)' }}>
            🚀 Deployment Progress {promotingContainer && `- ${promotingContainer}`}
          </h4>
          <div style={{ marginBottom: '10px' }}>
            <div style={{ 
              display: 'flex', 
              justifyContent: 'space-between', 
              alignItems: 'center',
              marginBottom: '5px'
            }}>
              <span style={{ fontWeight: 'bold', color: 'var(--text-primary)' }}>{deploymentProgress.message}</span>
              <span style={{ color: 'var(--text-secondary)' }}>{deploymentProgress.progress}%</span>
            </div>
            <div style={{
              width: '100%',
              height: '25px',
              backgroundColor: theme === 'dark' ? 'var(--bg-tertiary)' : '#e0e0e0',
              borderRadius: '5px',
              overflow: 'hidden',
              position: 'relative'
            }}>
              <div style={{
                width: `${deploymentProgress.progress}%`,
                height: '100%',
                backgroundColor: deploymentProgress.stage === 'completed' ? '#28a745' :
                                deploymentProgress.stage === 'failed' || deploymentProgress.stage === 'error' ? '#dc3545' :
                                deploymentProgress.stage === 'cancelled' ? '#ffc107' :
                                '#007bff',
                transition: 'width 0.3s ease',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'white',
                fontWeight: 'bold',
                fontSize: '12px'
              }}>
                {deploymentProgress.progress}%
              </div>
            </div>
            <div style={{ 
              marginTop: '5px', 
              fontSize: '12px', 
              color: 'var(--text-secondary)',
              fontStyle: 'italic',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <span>Stage: {deploymentProgress.stage}</span>
              {promotingContainer && 
               deploymentProgress.stage !== 'completed' && 
               deploymentProgress.stage !== 'failed' && 
               deploymentProgress.stage !== 'error' && 
               deploymentProgress.stage !== 'cancelled' && (
                <button
                  onClick={() => handleStopPromotion(promotingContainer)}
                  disabled={cancellingContainer === promotingContainer}
                  style={{
                    padding: '5px 15px',
                    backgroundColor: '#dc3545',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: cancellingContainer === promotingContainer ? 'not-allowed' : 'pointer',
                    fontSize: '12px',
                    fontWeight: 'bold',
                    opacity: cancellingContainer === promotingContainer ? 0.6 : 1
                  }}
                >
                  {cancellingContainer === promotingContainer ? 'Cancelling...' : '⏹️ Stop Deployment'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-title">Environment Promotion Workflow</h3>
        
        <div style={{ 
          display: 'flex', 
          justifyContent: 'center', 
          alignItems: 'center',
          gap: '2rem',
          flexWrap: 'wrap',
          padding: '2rem'
        }}>
          {environments.map((env, index) => (
            <React.Fragment key={env.name}>
              <div 
                onClick={() => handleEnvClick(env.name)}
                style={{
                textAlign: 'center',
                padding: '2rem',
                border: `2px solid ${env.color}`,
                borderRadius: '8px',
                minWidth: '200px',
                background: `linear-gradient(135deg, ${env.color}15 0%, ${env.color}25 50%, ${env.color}15 100%)`,
                boxShadow: `0 0 20px ${env.color}40, 0 0 40px ${env.color}20, inset 0 0 20px ${env.color}10`,
                position: 'relative',
                overflow: 'hidden',
                cursor: 'pointer',
                transition: 'all 0.2s ease'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'scale(1.05)'
                e.currentTarget.style.boxShadow = `0 0 30px ${env.color}60, 0 0 60px ${env.color}30, inset 0 0 30px ${env.color}15`
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'scale(1)'
                e.currentTarget.style.boxShadow = `0 0 20px ${env.color}40, 0 0 40px ${env.color}20, inset 0 0 20px ${env.color}10`
              }}
              >
                <div style={{
                  width: '20px',
                  height: '20px',
                  borderRadius: '50%',
                  backgroundColor: env.color,
                  margin: '0 auto 1rem',
                  border: '2px solid #fff',
                  boxShadow: `0 0 0 2px ${env.color}, 0 0 15px ${env.color}80`,
                  position: 'relative',
                  zIndex: 1
                }}></div>
                <h3 style={{ 
                  color: env.color, 
                  marginBottom: '0.5rem',
                  textShadow: `0 0 10px ${env.color}60`,
                  position: 'relative',
                  zIndex: 1
                }}>
                  {env.label}
                </h3>
                <p style={{ 
                  color: 'var(--text-secondary)', 
                  fontSize: '0.9rem',
                  position: 'relative',
                  zIndex: 1
                }}>
                  Image: {(() => {
                    const envData = getEnvironmentData(env.name)
                    if (loadingStatus) return 'Loading...'
                    if (envData?.primary_image) return envData.primary_image
                    if (envData?.images?.length > 0) return envData.images[0]
                    return 'No image'
                  })()}
                </p>
                <p style={{ 
                  color: 'var(--text-secondary)', 
                  fontSize: '0.9rem',
                  position: 'relative',
                  zIndex: 1
                }}>
                  Status: {(() => {
                    const envData = getEnvironmentData(env.name)
                    if (loadingStatus) return 'Loading...'
                    const status = envData?.status || 'empty'
                    return getStatusLabel(status)
                  })()}
                </p>
                {(() => {
                  const envData = getEnvironmentData(env.name)
                  if (!envData || loadingStatus) return null
                  return (
                    <p style={{ 
                      color: 'var(--text-secondary)', 
                      fontSize: '0.8rem',
                      position: 'relative',
                      zIndex: 1,
                      marginTop: '0.5rem'
                    }}>
                      Containers: {envData.containers?.running || 0} running / {envData.containers?.total || 0} total
                    </p>
                  )
                })()}
              </div>
              
              {index < environments.length - 1 && (
                <div style={{ 
                  fontSize: '2rem', 
                  color: '#667eea',
                  fontWeight: 'bold'
                }}>
                  →
                </div>
              )}
            </React.Fragment>
          ))}
        </div>

        <div style={{
          display: 'flex',
          justifyContent: 'center',
          gap: '1rem',
          marginTop: '2rem',
          flexWrap: 'wrap'
        }}>
          {environments.slice(0, -1).map((env, index) => {
            const nextEnv = environments[index + 1]
            const key = `${env.name}-${nextEnv.name}`
            // Colors based on operation importance
            // DEV → Pre-Prod: yellow/orange (medium importance)
            // Pre-Prod → PROD: red (high importance)
            const buttonColor = index === 0 
              ? '#f59e0b' // Orange for DEV → Pre-Prod
              : '#dc2626' // Czerwony dla Pre-Prod → PROD
            const buttonHoverColor = index === 0
              ? '#d97706' // Darker orange
              : '#b91c1c' // Ciemniejszy czerwony
            
            return (
              <button
                key={key}
                className="btn btn-primary"
                onClick={() => handlePromote(env.name, nextEnv.name)}
                disabled={loading[key]}
                style={{
                  background: buttonColor,
                  border: 'none',
                  color: 'white',
                  fontWeight: '600',
                  boxShadow: `0 4px 12px ${buttonColor}50`
                }}
                onMouseEnter={(e) => {
                  if (!loading[key]) {
                    e.target.style.background = buttonHoverColor
                    e.target.style.boxShadow = `0 6px 16px ${buttonColor}70`
                  }
                }}
                onMouseLeave={(e) => {
                  e.target.style.background = buttonColor
                  e.target.style.boxShadow = `0 4px 12px ${buttonColor}50`
                }}
              >
                {loading[key] 
                  ? 'Promoting...' 
                  : `${env.label} → ${nextEnv.label}`
                }
              </button>
            )
          })}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">Environment Configuration</h3>
        <div style={{ 
          padding: '1rem',
          backgroundColor: '#f5f5f5',
          borderRadius: '4px',
          fontFamily: 'monospace',
          fontSize: '0.9rem'
        }}>
          <p>Environment configuration is managed by DockerPilot.</p>
          <p>Use the buttons above to promote between environments.</p>
          <p style={{ marginTop: '1rem', color: '#666' }}>
            Promotion uses command: <code>dockerpilot promote &lt;from_env&gt; &lt;to_env&gt;</code>
          </p>
          <p style={{ marginTop: '1rem', color: '#666' }}>
            <strong>💡 Tip:</strong> Click an environment tile (DEV/Pre-Prod/PROD) to prepare container configs.
          </p>
        </div>
      </div>

      {/* Container Selection Modal */}
      {showContainerModal && (
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
        }} onClick={() => setShowContainerModal(false)}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '1.5rem',
            maxWidth: '700px',
            maxHeight: '80vh',
            width: '90%',
            overflow: 'auto',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
              <h3 style={{ color: 'var(--text-primary)' }}>
                Prepare configuration for environment {selectedEnv?.toUpperCase()}
              </h3>
              <button 
                onClick={() => setShowContainerModal(false)}
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

            {/* Mode selector */}
            <div style={{ 
              display: 'flex', 
              gap: '0.5rem', 
              marginBottom: '1rem',
              borderBottom: '1px solid var(--border-color)',
              paddingBottom: '1rem'
            }}>
              <button
                onClick={() => {
                  setConfigMode('generate')
                  setContainerSearch('')
                }}
                style={{
                  flex: 1,
                  padding: '0.5rem',
                  backgroundColor: configMode === 'generate' ? '#28a745' : 'var(--input-bg)',
                  color: configMode === 'generate' ? 'white' : 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: configMode === 'generate' ? '600' : 'normal'
                }}
              >
                📦 Generate from container
              </button>
              <button
                onClick={() => {
                  setConfigMode('select')
                  setBrowserPath('')
                  setFileSearch('')
                  setSelectedContainerForFile(null)
                  setShowFileBrowser(false)
                }}
                style={{
                  flex: 1,
                  padding: '0.5rem',
                  backgroundColor: configMode === 'select' ? '#28a745' : 'var(--input-bg)',
                  color: configMode === 'select' ? 'white' : 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: configMode === 'select' ? '600' : 'normal'
                }}
              >
                📁 Select from file
              </button>
              
              <button
                onClick={() => {
                  setConfigMode('stage-single')
                  setSelectedContainerForFile(null)
                  setShowFileBrowser(false)
                  setContainerSearch('')
                }}
                style={{
                  flex: 1,
                  padding: '0.5rem',
                  backgroundColor: configMode === 'stage-single' ? '#ffc107' : 'var(--input-bg)',
                  color: configMode === 'stage-single' ? 'white' : 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: configMode === 'stage-single' ? '600' : 'normal'
                }}
              >
                🚀 Promote single
              </button>
            </div>

            {configMode === 'stage-single' ? (
              <>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem', fontWeight: 'bold' }}>
                  🚀 Promote single: Select a container from {selectedEnv === 'dev' ? 'DEV' : selectedEnv === 'staging' ? 'Pre-Prod' : 'PROD'} to promote it to {selectedEnv === 'dev' ? 'Pre-Prod' : selectedEnv === 'staging' ? 'PROD' : '(none)'}
                </p>
                <p style={{ color: 'var(--text-tertiary)', marginBottom: '1rem', fontSize: '0.9rem' }}>
                  The container will be prepared and promoted automatically ({selectedEnv === 'dev' ? 'DEV → Pre-Prod' : selectedEnv === 'staging' ? 'Pre-Prod → PROD' : 'no action'})
                </p>

                <div style={{ marginBottom: '1rem' }}>
                  <input
                    type="text"
                    placeholder="Search containers..."
                    value={containerSearch}
                    onChange={(e) => setContainerSearch(e.target.value)}
                    style={{ 
                      width: '100%', 
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px'
                    }}
                  />
                </div>

                {loadingContainers ? (
                  <div style={{ textAlign: 'center', padding: '2rem' }}>Loading containers...</div>
                ) : (
                  <div style={{ 
                    border: '1px solid var(--border-color)', 
                    borderRadius: '4px',
                    maxHeight: '400px',
                    overflowY: 'auto',
                    backgroundColor: 'var(--bg-tertiary)'
                  }}>
                    {filteredContainers.length === 0 ? (
                      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                        {containerSearch ? 'No containers found matching search' : 'No running containers'}
                      </div>
                    ) : (
                      filteredContainers.map((container, idx) => (
                        <div
                          key={idx}
                          style={{
                            padding: '1rem',
                            borderBottom: '1px solid var(--border-color)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: '1rem',
                            backgroundColor: 'var(--card-bg)',
                            color: 'var(--text-primary)'
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'}
                          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--card-bg)'}
                        >
                          <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 'bold', fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                              {container.name}
                            </div>
                            <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
                              {container.image || 'No image'}
                            </div>
                            <div style={{ fontSize: '0.85rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                              Status: {container.status || container.state || 'unknown'}
                            </div>
                          </div>
                          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <button
                              onClick={() => handleMigrateContainer(container.name)}
                              disabled={loading[`migrate_${container.name}`]}
                              style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: loading[`migrate_${container.name}`] ? '#6c757d' : '#17a2b8',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: loading[`migrate_${container.name}`] ? 'not-allowed' : 'pointer',
                                fontWeight: '600',
                                fontSize: '0.85rem'
                              }}
                              title="Migrate container to another server"
                            >
                              {loading[`migrate_${container.name}`] ? '⏳ Migrating...' : '📦 Migrate'}
                            </button>
                            <button
                              onClick={() => handleStageSingleContainer(container.name)}
                              disabled={promotingContainer === container.name}
                              style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: promotingContainer === container.name ? '#6c757d' : '#ffc107',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: promotingContainer === container.name ? 'not-allowed' : 'pointer',
                                fontWeight: '600'
                              }}
                            >
                              {promotingContainer === container.name ? 'Promoting...' : `🚀 ${selectedEnv === 'dev' ? 'Pre-Prod' : 'PROD'}`}
                            </button>
                            
                            {promotingContainer === container.name && (
                              <button
                                onClick={() => handleStopPromotion(container.name)}
                                disabled={cancellingContainer === container.name}
                                style={{
                                  padding: '0.5rem 1rem',
                                  backgroundColor: cancellingContainer === container.name ? '#6c757d' : '#dc3545',
                                  color: 'white',
                                  border: 'none',
                                  borderRadius: '4px',
                                  cursor: cancellingContainer === container.name ? 'not-allowed' : 'pointer',
                                  fontWeight: '600',
                                  animation: 'pulse 2s infinite'
                                }}
                                title="Stop deployment"
                              >
                                {cancellingContainer === container.name ? '⏳ Cancelling...' : '🛑 STOP'}
                              </button>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </>
            ) : configMode === 'generate' ? (
              <>
                <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                  Select a container to automatically prepare the deployment-{selectedEnv}.yml config file
                </p>

                <div style={{ marginBottom: '1rem' }}>
                  <input
                    type="text"
                    placeholder="Search containers..."
                    value={containerSearch}
                    onChange={(e) => setContainerSearch(e.target.value)}
                    style={{ 
                      width: '100%', 
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px'
                    }}
                  />
                </div>

                {loadingContainers ? (
                  <div style={{ textAlign: 'center', padding: '2rem' }}>Loading containers...</div>
                ) : (
                  <div style={{ 
                    border: '1px solid var(--border-color)', 
                    borderRadius: '4px',
                    maxHeight: '400px',
                    overflowY: 'auto',
                    backgroundColor: 'var(--bg-tertiary)'
                  }}>
                    {filteredContainers.length === 0 ? (
                      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                        {containerSearch ? 'No containers found matching search' : 'No running containers'}
                      </div>
                    ) : (
                      filteredContainers.map((container, idx) => (
                        <div
                          key={idx}
                          style={{
                            padding: '1rem',
                            borderBottom: '1px solid var(--border-color)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: '1rem',
                            backgroundColor: 'var(--card-bg)',
                            color: 'var(--text-primary)'
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'}
                          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--card-bg)'}
                        >
                          <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 'bold', fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                              {container.name}
                            </div>
                            <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
                              {container.image || 'No image'}
                            </div>
                            <div style={{ fontSize: '0.85rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                              Status: {container.status || container.state || 'unknown'}
                            </div>
                          </div>
                          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <button
                              onClick={() => handleMigrateContainer(container.name)}
                              disabled={loading[`migrate_${container.name}`]}
                              style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: loading[`migrate_${container.name}`] ? '#6c757d' : '#17a2b8',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: loading[`migrate_${container.name}`] ? 'not-allowed' : 'pointer',
                                fontWeight: '600',
                                fontSize: '0.85rem'
                              }}
                              title="Migruj kontener na inny serwer"
                            >
                              {loading[`migrate_${container.name}`] ? '⏳ Migrating...' : '📦 Migrate'}
                            </button>
                            <button
                              onClick={() => handlePrepareConfig(container.name)}
                              disabled={preparingConfig}
                              style={{
                                padding: '0.5rem 1rem',
                                backgroundColor: preparingConfig ? '#6c757d' : '#28a745',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: preparingConfig ? 'not-allowed' : 'pointer',
                                fontWeight: '600'
                              }}
                            >
                              {preparingConfig ? 'Preparing...' : 'Prepare'}
                            </button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </>
            ) : (
              <>
                {!selectedContainerForFile ? (
                  <>
                    <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                      Select a container in this environment to import a configuration file
                    </p>
                    
                    <div style={{ marginBottom: '1rem' }}>
                      <input
                        type="text"
                        placeholder="Search containers..."
                        value={containerSearch}
                        onChange={(e) => setContainerSearch(e.target.value)}
                        style={{ 
                          width: '100%', 
                          padding: '0.5rem',
                          backgroundColor: 'var(--input-bg)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--input-border)',
                          borderRadius: '4px'
                        }}
                      />
                    </div>

                    {loadingContainers ? (
                      <div style={{ textAlign: 'center', padding: '2rem' }}>Loading containers...</div>
                    ) : (
                      <div style={{ 
                        border: '1px solid var(--border-color)', 
                        borderRadius: '4px',
                        maxHeight: '400px',
                        overflowY: 'auto',
                        backgroundColor: 'var(--bg-tertiary)'
                      }}>
                        {filteredContainers.length === 0 ? (
                          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                            {containerSearch ? 'No containers found matching search' : 'No running containers in this environment'}
                          </div>
                        ) : (
                          filteredContainers.map((container, idx) => (
                            <div
                              key={idx}
                              style={{
                                padding: '1rem',
                                borderBottom: '1px solid var(--border-color)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: '1rem',
                                backgroundColor: 'var(--card-bg)',
                                color: 'var(--text-primary)'
                              }}
                              onMouseEnter={(e) => e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'}
                              onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'var(--card-bg)'}
                            >
                              <div style={{ flex: 1 }}>
                                <div style={{ fontWeight: 'bold', fontSize: '1.1rem', marginBottom: '0.25rem' }}>
                                  {container.name}
                                </div>
                                <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
                                  {container.image || 'No image'}
                                </div>
                                <div style={{ fontSize: '0.85rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                                  Status: {container.status || container.state || 'unknown'}
                                </div>
                              </div>
                              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                <button
                                  onClick={() => handleMigrateContainer(container.name)}
                                  disabled={loading[`migrate_${container.name}`]}
                                  style={{
                                    padding: '0.5rem 1rem',
                                    backgroundColor: loading[`migrate_${container.name}`] ? '#6c757d' : '#17a2b8',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: loading[`migrate_${container.name}`] ? 'not-allowed' : 'pointer',
                                    fontWeight: '600',
                                    fontSize: '0.85rem'
                                  }}
                                  title="Migrate container to another server"
                                >
                                  {loading[`migrate_${container.name}`] ? '⏳ Migrating...' : '📦 Migrate'}
                                </button>
                                <button
                                  onClick={() => handleSelectContainerForFile(container.name)}
                                  style={{
                                    padding: '0.5rem 1rem',
                                    backgroundColor: '#007bff',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontWeight: '600'
                                  }}
                                >
                                  📁 Select file
                                </button>
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div style={{ 
                      marginBottom: '1rem', 
                      padding: '0.75rem', 
                      backgroundColor: 'var(--bg-tertiary)', 
                      borderRadius: '4px',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center'
                    }}>
                      <div>
                        <strong style={{ color: 'var(--text-primary)' }}>Container:</strong>{' '}
                        <span style={{ color: 'var(--text-secondary)' }}>{selectedContainerForFile}</span>
                      </div>
                      <button
                        onClick={() => {
                          setSelectedContainerForFile(null)
                          setShowFileBrowser(false)
                        }}
                        style={{
                          padding: '0.25rem 0.5rem',
                          backgroundColor: 'var(--input-bg)',
                          border: '1px solid var(--border-color)',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          color: 'var(--text-primary)'
                        }}
                      >
                        ← Back
                      </button>
                    </div>
                    <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                      Select the deployment-*.yml config file for container {selectedContainerForFile}
                    </p>

                {/* File browser navigation */}
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
                      style={{ 
                        padding: '0.25rem 0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        border: '1px solid var(--border-color)',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        color: 'var(--text-primary)'
                      }}
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
                      placeholder="Path..."
                    />
                    <button 
                      onClick={() => loadFileBrowser(browserPath)}
                      style={{ 
                        padding: '0.5rem 1rem',
                        backgroundColor: '#007bff',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer'
                      }}
                    >
                      Go
                    </button>
                  </div>
                  <input
                    type="text"
                    placeholder="Search deployment-*.yml files..."
                    value={fileSearch}
                    onChange={(e) => setFileSearch(e.target.value)}
                    style={{ 
                      width: '100%', 
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px'
                    }}
                  />
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
                    {filteredFiles.length === 0 ? (
                      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                        {fileSearch ? 'No matching files found' : 'No deployment-*.yml files in this directory'}
                      </div>
                    ) : (
                      filteredFiles.map((item, idx) => (
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
                            backgroundColor: !item.is_dir && (item.name.toLowerCase().includes('deployment') && (item.name.toLowerCase().endsWith('.yml') || item.name.toLowerCase().endsWith('.yaml')))
                              ? 'rgba(40, 167, 69, 0.1)'
                              : 'var(--card-bg)',
                            color: 'var(--text-primary)'
                          }}
                          onMouseEnter={(e) => {
                            if (item.is_dir || (!item.is_dir && (item.name.toLowerCase().includes('deployment') && (item.name.toLowerCase().endsWith('.yml') || item.name.toLowerCase().endsWith('.yaml'))))) {
                              e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)'
                            }
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.backgroundColor = !item.is_dir && (item.name.toLowerCase().includes('deployment') && (item.name.toLowerCase().endsWith('.yml') || item.name.toLowerCase().endsWith('.yaml')))
                              ? 'rgba(40, 167, 69, 0.1)'
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
                          {!item.is_dir && item.name.toLowerCase().includes('deployment') && (item.name.toLowerCase().endsWith('.yml') || item.name.toLowerCase().endsWith('.yaml')) && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                handleImportConfig(item.path, selectedContainerForFile)
                              }}
                              disabled={importingConfig || !selectedContainerForFile}
                              style={{
                                padding: '0.25rem 0.5rem',
                                backgroundColor: (importingConfig || !selectedContainerForFile) ? '#6c757d' : '#28a745',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: (importingConfig || !selectedContainerForFile) ? 'not-allowed' : 'pointer',
                                fontWeight: '600'
                              }}
                            >
                              {importingConfig ? 'Importowanie...' : 'Importuj'}
                            </button>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
                </>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Server Add/Edit Modal */}
      {showServerModal && (
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
        }} onClick={() => {
          setShowServerModal(false)
          resetServerForm()
        }}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '2rem',
            maxWidth: '600px',
            maxHeight: '90vh',
            width: '90%',
            overflow: 'auto',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <h3 style={{ color: 'var(--text-primary)', margin: 0 }}>
                {editingServer ? 'Edit Server' : 'Add New Server'}
              </h3>
              <button
                onClick={() => {
                  setShowServerModal(false)
                  resetServerForm()
                }}
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

            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                  Nazwa serwera *
                </label>
                <input
                  type="text"
                  value={serverForm.name}
                  onChange={(e) => setServerForm({ ...serverForm, name: e.target.value })}
                  placeholder="np. Production Server"
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              <div style={{ display: 'flex', gap: '1rem' }}>
                <div style={{ flex: 2 }}>
                  <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                    Hostname/IP *
                  </label>
                  <input
                    type="text"
                    value={serverForm.hostname}
                    onChange={(e) => setServerForm({ ...serverForm, hostname: e.target.value })}
                    placeholder="192.168.1.100 lub example.com"
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                    Port
                  </label>
                  <input
                    type="number"
                    value={serverForm.port}
                    onChange={(e) => setServerForm({ ...serverForm, port: parseInt(e.target.value) || 22 })}
                    placeholder="22"
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>
              </div>

              <div>
                <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                  User *
                </label>
                <input
                  type="text"
                  value={serverForm.username}
                  onChange={(e) => setServerForm({ ...serverForm, username: e.target.value })}
                  placeholder="root"
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              <div>
                <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                  Metoda autentykacji *
                </label>
                <select
                  value={serverForm.auth_type}
                  onChange={(e) => setServerForm({ ...serverForm, auth_type: e.target.value })}
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    boxSizing: 'border-box'
                  }}
                >
                  <option value="password">Password</option>
                  <option value="key">SSH Key</option>
                  <option value="2fa">Password + 2FA</option>
                </select>
              </div>

              {serverForm.auth_type === 'password' && (
                <div>
                  <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                    Password *
                  </label>
                  <input
                    type="password"
                    value={serverForm.password}
                    onChange={(e) => setServerForm({ ...serverForm, password: e.target.value })}
                    placeholder="Enter password"
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      backgroundColor: 'var(--input-bg)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--input-border)',
                      borderRadius: '4px',
                      boxSizing: 'border-box'
                    }}
                  />
                </div>
              )}

              {serverForm.auth_type === 'key' && (
                <>
                  <div>
                    <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                      Klucz prywatny SSH *
                    </label>
                    <input
                      type="file"
                      accept=".key,.pem,.ppk"
                      onChange={handleKeyFileUpload}
                      style={{
                        width: '100%',
                        padding: '0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--input-border)',
                        borderRadius: '4px',
                        boxSizing: 'border-box'
                      }}
                    />
                    <textarea
                      value={serverForm.private_key}
                      onChange={(e) => setServerForm({ ...serverForm, private_key: e.target.value })}
                      placeholder="Paste the private key contents (OpenSSH format) or choose a file"
                      rows={6}
                      style={{
                        width: '100%',
                        padding: '0.5rem',
                        marginTop: '0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--input-border)',
                        borderRadius: '4px',
                        fontFamily: 'monospace',
                        fontSize: '0.85rem',
                        boxSizing: 'border-box'
                      }}
                    />
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                      Supported formats: OpenSSH (.key, .pem). For PuTTY (.ppk), convert first in PuTTYgen.
                    </div>
                  </div>
                  <div>
                    <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                      Key password (optional)
                    </label>
                    <input
                      type="password"
                      value={serverForm.key_passphrase}
                      onChange={(e) => setServerForm({ ...serverForm, key_passphrase: e.target.value })}
                      placeholder="If key is password protected"
                      style={{
                        width: '100%',
                        padding: '0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--input-border)',
                        borderRadius: '4px',
                        boxSizing: 'border-box'
                      }}
                    />
                  </div>
                </>
              )}

              {serverForm.auth_type === '2fa' && (
                <>
                  <div>
                    <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                      Password *
                    </label>
                    <input
                      type="password"
                      value={serverForm.password}
                      onChange={(e) => setServerForm({ ...serverForm, password: e.target.value })}
                      placeholder="Enter password"
                      style={{
                        width: '100%',
                        padding: '0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--input-border)',
                        borderRadius: '4px',
                        boxSizing: 'border-box'
                      }}
                    />
                  </div>
                  <div>
                    <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                      Kod 2FA
                    </label>
                    <input
                      type="text"
                      value={serverForm.totp_code || ''}
                      onChange={(e) => setServerForm({ ...serverForm, totp_code: e.target.value })}
                      placeholder="Enter 2FA code (will be required for each connection)"
                      style={{
                        width: '100%',
                        padding: '0.5rem',
                        backgroundColor: 'var(--input-bg)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--input-border)',
                        borderRadius: '4px',
                        boxSizing: 'border-box'
                      }}
                    />
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
                      2FA code will be required for each connection. Enter it when testing the connection.
                    </div>
                  </div>
                </>
              )}

              <div>
                <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                  Opis (opcjonalnie)
                </label>
                <input
                  type="text"
                  value={serverForm.description}
                  onChange={(e) => setServerForm({ ...serverForm, description: e.target.value })}
                  placeholder="Short server description"
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px',
                    boxSizing: 'border-box'
                  }}
                />
              </div>

              {testingServerResult && (
                <div style={{
                  padding: '0.75rem',
                  backgroundColor: testingServerResult.success ? 'rgba(40, 167, 69, 0.1)' : 'rgba(220, 53, 69, 0.1)',
                  border: `1px solid ${testingServerResult.success ? '#28a745' : '#dc3545'}`,
                  borderRadius: '4px',
                  color: testingServerResult.success ? '#28a745' : '#dc3545'
                }}>
                  {testingServerResult.success ? '✓ ' : '✗ '}
                  {testingServerResult.message || testingServerResult.error || 'Test result'}
                </div>
              )}

              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
                <button
                  onClick={() => handleServerTest(null, serverForm)}
                  disabled={testingServer || !serverForm.hostname || !serverForm.username}
                  style={{
                    padding: '0.5rem 1rem',
                    backgroundColor: '#28a745',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: (testingServer || !serverForm.hostname || !serverForm.username) ? 'not-allowed' : 'pointer',
                    fontWeight: '600',
                    opacity: (testingServer || !serverForm.hostname || !serverForm.username) ? 0.6 : 1
                  }}
                >
                  {testingServer ? 'Testing...' : 'Test Connection'}
                </button>
                <button
                  onClick={() => {
                    setShowServerModal(false)
                    resetServerForm()
                  }}
                  style={{
                    padding: '0.5rem 1rem',
                    backgroundColor: '#6c757d',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontWeight: '600'
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={editingServer ? handleServerUpdate : handleServerCreate}
                  disabled={!serverForm.name || !serverForm.hostname || !serverForm.username}
                  style={{
                    padding: '0.5rem 1rem',
                    backgroundColor: (!serverForm.name || !serverForm.hostname || !serverForm.username) ? '#ccc' : '#007bff',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: (!serverForm.name || !serverForm.hostname || !serverForm.username) ? 'not-allowed' : 'pointer',
                    fontWeight: '600'
                  }}
                >
                  {editingServer ? 'Save' : 'Create'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Sudo Password Modal */}
      {showSudoModal ? (
        <div 
          id="sudo-password-modal"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: theme === 'dark' ? 'rgba(0, 0, 0, 0.9)' : 'rgba(0, 0, 0, 0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 999999,
            pointerEvents: 'auto'
          }}
          onLoad={() => console.log('[Modal] Modal loaded')}
          ref={(el) => {
            if (el) {
              console.log('[Modal] Modal element mounted:', el)
            }
          }}
          onClick={(e) => {
            // Close modal if clicking on backdrop
            if (e.target.id === 'sudo-password-modal') {
              console.log('[Modal] Backdrop clicked, closing modal')
              setShowSudoModal(false)
              setSudoPassword('')
              setSudoModalCallback(null)
            }
          }}
        >
          <div 
            style={{
              backgroundColor: theme === 'dark' ? 'var(--card-bg)' : 'white',
              padding: '2rem',
              borderRadius: '8px',
              maxWidth: '500px',
              width: '90%',
              boxShadow: theme === 'dark' ? '0 4px 20px rgba(0, 0, 0, 0.6)' : '0 4px 20px rgba(0, 0, 0, 0.3)',
              zIndex: 100000,
              position: 'relative',
              color: 'var(--text-primary)'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {(() => {
              try {
                const mountInfo = JSON.parse(sudoPassword || '{}')
                const requiresSudo = mountInfo.requires_sudo
                const hasLargeMounts = mountInfo.has_large_mounts
                const totalSizeTB = mountInfo.total_size_tb || 0
                
                return (
                  <>
                    <h2 style={{ marginTop: 0, color: requiresSudo ? '#dc3545' : '#ff9800' }}>
                      {requiresSudo && hasLargeMounts ? '🔐 Sudo password required + ⚠️ Large disks' :
                       requiresSudo ? '🔐 Sudo password required' :
                       hasLargeMounts ? '⚠️ Large disks detected' :
                       '🔐 Backup configuration'}
                    </h2>
                    <p style={{ marginBottom: '1rem', color: 'var(--text-primary)' }}>
                      {requiresSudo && hasLargeMounts ? 
                        `Backup of container ${sudoModalContainerName || 'container'} requires administrator privileges and large disks detected (${totalSizeTB.toFixed(2)} TB). Backup may take a very long time!` :
                       requiresSudo ?
                        `Backup of container ${sudoModalContainerName || 'container'} requires administrator privileges. Enter sudo password:` :
                       hasLargeMounts ?
                        `⚠️ Large disks detected (${totalSizeTB.toFixed(2)} TB) for container ${sudoModalContainerName || 'container'}. Backup may take a very long time (hours)!` :
                        `Backup configuration for container ${sudoModalContainerName || 'container'}:`}
                    </p>
                    
                    {/* Show large mounts warning */}
                    {hasLargeMounts && mountInfo.large_mounts && mountInfo.large_mounts.length > 0 && (
                      <div style={{
                        backgroundColor: theme === 'dark' ? '#3d2817' : '#fff3cd',
                        padding: '0.75rem',
                        borderRadius: '4px',
                        marginBottom: '1rem',
                        fontSize: '0.9rem',
                        color: theme === 'dark' ? '#ff9800' : '#856404',
                        border: `1px solid ${theme === 'dark' ? '#ff9800' : '#ffc107'}`
                      }}>
                        <strong>⚠️ Large disks detected (total {totalSizeTB.toFixed(2)} TB):</strong>
                        <ul style={{ margin: '0.5rem 0', paddingLeft: '1.5rem' }}>
                          {mountInfo.large_mounts.slice(0, 3).map((mount, idx) => (
                            <li key={idx}>
                              {mount.path}: {mount.size_tb?.toFixed(2) || 0} TB
                            </li>
                          ))}
                        </ul>
                        <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', marginBottom: 0 }}>
                          Backing up such large disks can take many hours. Consider skipping the backup.
                        </p>
                      </div>
                    )}
                    
                    {/* Show privileged paths if sudo required */}
                    {privilegedPaths.length > 0 && (
                      <div style={{
                        backgroundColor: theme === 'dark' ? 'rgba(255, 193, 7, 0.2)' : '#fff3cd',
                        padding: '0.75rem',
                        borderRadius: '4px',
                        marginBottom: '1rem',
                        fontSize: '0.9rem',
                        color: 'var(--text-primary)',
                        border: theme === 'dark' ? '1px solid rgba(255, 193, 7, 0.3)' : 'none'
                      }}>
                        <strong>Required paths (sudo):</strong>
                        <ul style={{ margin: '0.5rem 0', paddingLeft: '1.5rem', color: 'var(--text-primary)' }}>
                          {privilegedPaths.slice(0, 5).map((path, idx) => (
                            <li key={idx}>{path}</li>
                          ))}
                          {privilegedPaths.length > 5 && <li>... and {privilegedPaths.length - 5} more</li>}
                        </ul>
                      </div>
                    )}
                  </>
                )
              } catch {
                return (
                  <>
                    <h2 style={{ marginTop: 0, color: '#dc3545' }}>🔐 Sudo password required</h2>
                    <p style={{ marginBottom: '1rem', color: 'var(--text-primary)' }}>
                      Backup of container <strong>{sudoModalContainerName || 'container'}</strong> requires administrator privileges. Enter sudo password:
                    </p>
                    {privilegedPaths.length > 0 && (
                      <div style={{
                        backgroundColor: theme === 'dark' ? 'rgba(255, 193, 7, 0.2)' : '#fff3cd',
                        padding: '0.75rem',
                        borderRadius: '4px',
                        marginBottom: '1rem',
                        fontSize: '0.9rem',
                        color: 'var(--text-primary)',
                        border: theme === 'dark' ? '1px solid rgba(255, 193, 7, 0.3)' : 'none'
                      }}>
                        <strong>Required paths:</strong>
                        <ul style={{ margin: '0.5rem 0', paddingLeft: '1.5rem', color: 'var(--text-primary)' }}>
                          {privilegedPaths.slice(0, 5).map((path, idx) => (
                            <li key={idx}>{path}</li>
                          ))}
                          {privilegedPaths.length > 5 && <li>... and {privilegedPaths.length - 5} more</li>}
                        </ul>
                      </div>
                    )}
                  </>
                )
              }
            })()}
            
            {(() => {
              try {
                const mountInfo = JSON.parse(sudoPassword || '{}')
                if (mountInfo.requires_sudo) {
                  return (
                    <input
                      type="password"
                      value={(() => {
                        try {
                          const info = JSON.parse(sudoPassword || '{}')
                          return info.password || ''
                        } catch {
                          return ''
                        }
                      })()}
                      onChange={(e) => {
                        try {
                          const info = JSON.parse(sudoPassword || '{}')
                          info.password = e.target.value
                          setSudoPassword(JSON.stringify(info))
                        } catch {
                          // If parsing fails, just store the password
                          setSudoPassword(JSON.stringify({ password: e.target.value, requires_sudo: true }))
                        }
                      }}
                      placeholder="Enter sudo password"
                      style={{
                        width: '100%',
                        padding: '0.75rem',
                        fontSize: '1rem',
                        border: `1px solid ${theme === 'dark' ? 'var(--input-border)' : '#ccc'}`,
                        borderRadius: '4px',
                        marginBottom: '1rem',
                        boxSizing: 'border-box',
                        backgroundColor: theme === 'dark' ? 'var(--input-bg)' : 'white',
                        color: 'var(--text-primary)'
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          try {
                            const info = JSON.parse(sudoPassword || '{}')
                            if (info.password || !info.requires_sudo) {
                              sudoModalCallback && sudoModalCallback(sudoPassword, false)
                            }
                          } catch {
                            if (sudoPassword) {
                              sudoModalCallback && sudoModalCallback(sudoPassword, false)
                            }
                          }
                        }
                      }}
                      autoFocus
                    />
                  )
                }
              } catch {}
              return null
            })()}
            
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button
                onClick={async () => {
                  console.log('[Modal] Skip backup button clicked')
                  // Use ref for synchronous access to callback
                  const callback = sudoModalCallbackRef.current || sudoModalCallback
                  console.log('[Modal] sudoModalCallback from ref:', !!sudoModalCallbackRef.current)
                  console.log('[Modal] sudoModalCallback from state:', !!sudoModalCallback)
                  
                  if (!callback) {
                    console.error('[Modal] ERROR: sudoModalCallback is null! Cannot skip backup.')
                    setMessage({ type: 'error', text: 'Error: Callback is not available. Try again or refresh the page.' })
                    return
                  }
                  
                  if (window.confirm('Are you sure you want to skip backup? This is risky!')) {
                    console.log('[Modal] User confirmed skip backup, calling callback')
                    try {
                      await callback('', true)
                      console.log('[Modal] Skip backup callback completed')
                    } catch (error) {
                      console.error('[Modal] Error in skip backup callback:', error)
                      setMessage({ type: 'error', text: 'Error: ' + (error.message || 'Unknown error') })
                    }
                  }
                }}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: '#ffc107',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: '600'
                }}
              >
                Skip backup
              </button>
              <button
                onClick={() => {
                  console.log('[Modal] Cancel button clicked')
                  setShowSudoModal(false)
                  setSudoPassword('')
                  sudoModalCallbackRef.current = null
                setSudoModalCallback(null)
                }}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: '#6c757d',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontWeight: '600'
                }}
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  console.log('[Modal] Continue button clicked, password length:', sudoPassword.length)
                  // Use ref for synchronous access to callback
                  const callback = sudoModalCallbackRef.current || sudoModalCallback
                  console.log('[Modal] sudoModalCallback from ref:', !!sudoModalCallbackRef.current)
                  console.log('[Modal] sudoModalCallback from state:', !!sudoModalCallback)
                  console.log('[Modal] Using callback:', !!callback)
                  
                  if (!callback) {
                    console.error('[Modal] ERROR: sudoModalCallback is null in both ref and state!')
                    setMessage({ type: 'error', text: 'Error: Callback is not available. Try again or refresh the page.' })
                    return
                  }
                  
                  try {
                    console.log('[Modal] Calling callback with password...')
                    await callback(sudoPassword, false)
                    console.log('[Modal] Callback completed successfully')
                  } catch (error) {
                    console.error('[Modal] Error in callback:', error)
                    setMessage({ type: 'error', text: 'Error: ' + (error.message || 'Unknown error') })
                  }
                }}
                disabled={!sudoPassword}
                style={{
                  padding: '0.5rem 1rem',
                  backgroundColor: sudoPassword ? '#28a745' : '#ccc',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: sudoPassword ? 'pointer' : 'not-allowed',
                  fontWeight: '600'
                }}
              >
                Kontynuuj
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Migrate Container Modal */}
      {showMigrateModal && (
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
        }} onClick={() => {
          setShowMigrateModal(false)
          setMigratingContainer(null)
        }}>
          <div style={{
            backgroundColor: 'var(--card-bg)',
            borderRadius: '8px',
            padding: '2rem',
            maxWidth: '600px',
            width: '90%',
            boxShadow: '0 4px 20px var(--shadow-hover)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)'
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <h3 style={{ color: 'var(--text-primary)', margin: 0 }}>
                📦 Container Migration
              </h3>
              <button
                onClick={() => {
                  setShowMigrateModal(false)
                  setMigratingContainer(null)
                }}
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
              <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                Migruj kontener <strong>{migratingContainer}</strong> na inny serwer.
              </p>
              <p style={{ color: 'var(--text-tertiary)', fontSize: '0.9rem', marginBottom: '1rem' }}>
                Source server: <strong>{selectedServer === 'local' ? '🏠 Local' : servers.find(s => s.id === selectedServer)?.name || selectedServer}</strong>
              </p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ display: 'block', marginBottom: '0.5rem', color: 'var(--text-primary)', fontWeight: '600' }}>
                  Target server *
                </label>
                <select
                  value={migrationTargetServer}
                  onChange={(e) => setMigrationTargetServer(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    backgroundColor: 'var(--input-bg)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--input-border)',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    boxSizing: 'border-box'
                  }}
                >
                  <option value="">-- Select server --</option>
                  <option value="local">🏠 Local</option>
                  {servers.map((server) => (
                    server.id !== selectedServer && (
                      <option key={server.id} value={server.id}>
                        {server.name} ({server.hostname})
                      </option>
                    )
                  ))}
                </select>
              </div>

              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={migrationIncludeData}
                    onChange={(e) => setMigrationIncludeData(e.target.checked)}
                    style={{ cursor: 'pointer' }}
                  />
                  <span style={{ color: 'var(--text-primary)' }}>
                    Include container data (volumes) - may be slow for large volumes
                  </span>
                </label>
              </div>

              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={migrationStopSource}
                    onChange={(e) => setMigrationStopSource(e.target.checked)}
                    style={{ cursor: 'pointer' }}
                  />
                  <span style={{ color: 'var(--text-primary)' }}>
                    Stop source container after migration
                  </span>
                </label>
              </div>

              <div style={{ 
                padding: '0.75rem', 
                backgroundColor: 'var(--bg-tertiary)', 
                borderRadius: '4px',
                fontSize: '0.9rem',
                color: 'var(--text-secondary)'
              }}>
                <strong>ℹ️ Information:</strong> Migration exports Docker image from container and its configuration (ports, environment variables, volumes), then imports and starts container on target server.
              </div>

              {/* Migration Progress Bar */}
              {migrationProgress && (
                <div style={{ 
                  marginBottom: '1rem',
                  padding: '1rem',
                  backgroundColor: theme === 'dark' ? 'var(--bg-tertiary)' : '#f8f9fa',
                  borderRadius: '8px',
                  border: '1px solid var(--border-color)'
                }}>
                  <div style={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center',
                    marginBottom: '8px'
                  }}>
                    <span style={{ fontWeight: 'bold', color: 'var(--text-primary)', fontSize: '0.95rem' }}>
                      {migrationProgress.message}
                    </span>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                      {migrationProgress.progress}%
                    </span>
                  </div>
                  <div style={{
                    width: '100%',
                    height: '20px',
                    backgroundColor: theme === 'dark' ? 'var(--bg-secondary)' : '#e0e0e0',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    position: 'relative'
                  }}>
                    <div style={{
                      width: `${migrationProgress.progress}%`,
                      height: '100%',
                      backgroundColor: migrationProgress.stage === 'completed' ? '#28a745' :
                                      migrationProgress.stage === 'failed' || migrationProgress.stage === 'error' ? '#dc3545' :
                                      migrationProgress.stage === 'cancelled' ? '#ffc107' :
                                      '#17a2b8',
                      transition: 'width 0.3s ease',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'white',
                      fontWeight: 'bold',
                      fontSize: '11px'
                    }}>
                      {migrationProgress.progress}%
                    </div>
                  </div>
                  <div style={{ 
                    marginTop: '5px', 
                    fontSize: '11px', 
                    color: 'var(--text-secondary)',
                    fontStyle: 'italic'
                  }}>
                    Etap: {migrationProgress.stage}
                  </div>
                </div>
              )}

              <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1rem' }}>
                {migrationProgress && migrationProgress.stage !== 'completed' && migrationProgress.stage !== 'failed' && migrationProgress.stage !== 'cancelled' && (
                  <button
                    onClick={handleCancelMigration}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: '#dc3545',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontWeight: '600'
                    }}
                  >
                    🛑 Cancel migration
                  </button>
                )}
                <button
                  onClick={() => {
                    if (migrationProgress && migrationProgress.stage !== 'completed' && migrationProgress.stage !== 'failed' && migrationProgress.stage !== 'cancelled') {
                      handleCancelMigration()
                    }
                    setShowMigrateModal(false)
                    setMigratingContainer(null)
                    setMigrationProgress(null)
                  }}
                  style={{
                    padding: '0.5rem 1rem',
                    backgroundColor: '#6c757d',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontWeight: '600'
                  }}
                >
                  {migrationProgress && migrationProgress.stage !== 'completed' && migrationProgress.stage !== 'failed' && migrationProgress.stage !== 'cancelled' ? 'Zamknij' : 'Zamknij'}
                </button>
                {!migrationProgress && (
                  <button
                    onClick={executeMigration}
                    disabled={!migrationTargetServer || loading[`migrate_${migratingContainer}`]}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: (!migrationTargetServer || loading[`migrate_${migratingContainer}`]) ? '#ccc' : '#17a2b8',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: (!migrationTargetServer || loading[`migrate_${migratingContainer}`]) ? 'not-allowed' : 'pointer',
                      fontWeight: '600'
                    }}
                  >
                    {loading[`migrate_${migratingContainer}`] ? 'Migrating...' : 'Start migration'}
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Environments

