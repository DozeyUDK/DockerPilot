import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Pipeline API
export const pipelineAPI = {
  generate: (data) => api.post('/pipeline/generate', data),
  save: (data) => api.post('/pipeline/save', data)
}

// Environment API
export const environmentAPI = {
  promote: (fromEnv, toEnv, skipBackup = false) => api.post('/environment/promote', { 
    from_env: fromEnv, 
    to_env: toEnv,
    skip_backup: skipBackup 
  }),
  promoteSingle: (
    fromEnv,
    toEnv,
    containerName,
    skipBackup = false,
    includeData = true,
    elevationToken = null
  ) => api.post('/environment/promote-single', {
    from_env: fromEnv,
    to_env: toEnv,
    container_name: containerName,
    skip_backup: skipBackup,
    include_data: includeData,
    elevation_token: elevationToken || undefined
  }),
  getProgress: (containerName) => api.get('/environment/progress', {
    params: { container_name: containerName }
  }),
  getAllActiveDeployments: () => api.get('/environment/progress'),
  checkSudo: (containerName) => api.post('/environment/check-sudo', {
    container_name: containerName
  }),
  requestElevationToken: (password, scope = {}) => api.post('/environment/elevation-token', {
    sudo_password: password,
    scope
  }, { withCredentials: true }),
  clearElevationTokens: () => api.delete('/environment/elevation-token', { withCredentials: true }),
  setSudoPassword: (password) => api.post('/environment/sudo-password', {
    sudo_password: password
  }, { withCredentials: true }),
  clearSudoPassword: () => api.delete('/environment/sudo-password', { withCredentials: true }),
  cancelPromotion: (containerName) => api.post('/environment/cancel-promotion', {
    container_name: containerName
  }),
  getStatus: () => api.get('/environment/status'),
  prepareConfig: (containerName, targetEnv, sourceEnv = null) => api.post('/environment/prepare-config', { 
    container_name: containerName, 
    target_env: targetEnv,
    source_env: sourceEnv
  }),
  importConfig: (configFilePath, targetEnv, containerName = null) => api.post('/environment/import-config', {
    config_file_path: configFilePath,
    target_env: targetEnv,
    container_name: containerName  // Optional - if provided, will override container_name from file
  }),
  getEnvServersMap: () => api.get('/environment/servers-map'),
  updateEnvServersMap: (envServers) => api.put('/environment/servers-map', { env_servers: envServers }),
  getContainerBindings: () => api.get('/environment/container-bindings'),
  updateContainerBindings: (envContainers) =>
    api.put('/environment/container-bindings', { env_containers: envContainers })
}

// Status API
export const statusAPI = {
  check: () => api.get('/status'),
  preflight: () => api.get('/preflight'),
  containers: () => api.get('/containers'),
  executeCommand: (program, command, workingDirectory) => api.post('/command/execute', { 
    program, 
    command,
    working_directory: workingDirectory 
  }),
  getCommandHelp: (program) => api.get('/command/help', { params: { program } }),
  migrateContainer: (containerName, sourceServerId, targetServerId, includeData = false, stopSource = false) => 
    api.post('/containers/migrate', {
      container_name: containerName,
      source_server_id: sourceServerId,
      target_server_id: targetServerId,
      include_data: includeData,
      stop_source: stopSource
    }),
  getMigrationProgress: (containerName) => api.get('/containers/migration-progress', {
    params: { container_name: containerName }
  }),
  cancelMigration: (containerName) => api.post('/containers/cancel-migration', {
    container_name: containerName
  })
}

// Storage API
export const storageAPI = {
  status: () => api.get('/storage/status'),
  testPostgres: (postgres, ensureSchema = true) =>
    api.post('/storage/test-postgres', { postgres, ensure_schema: ensureSchema }),
  discoverLocalPostgres: (containerName = 'postgres-dozeyserver') =>
    api.get('/storage/discover-local-postgres', { params: { container_name: containerName } }),
  bootstrapLocalPostgres: (payload = {}) =>
    api.post('/storage/bootstrap-local-postgres', payload),
  configure: (payload = {}) =>
    api.post('/storage/configure', payload)
}

// Docker API
export const dockerAPI = {
  images: () => api.get('/docker/images'),
  dockerfiles: () => api.get('/docker/dockerfiles')
}

// File Browser API
export const fileBrowserAPI = {
  browse: (path) => api.get('/files/browse', { params: { path } })
}

// Servers API
export const serversAPI = {
  list: () => api.get('/servers'),
  create: (serverData) => api.post('/servers/create', serverData),
  update: (serverId, serverData) => api.put(`/servers/${serverId}`, serverData),
  delete: (serverId) => api.delete(`/servers/${serverId}`),
  test: (serverId, testData = null) => {
    if (serverId) {
      return api.post(`/servers/${serverId}/test`, testData || {})
    } else {
      return api.post('/servers/test', testData || {})
    }
  },
  select: (serverId, setAsDefault = false) => api.post('/servers/select', { 
    server_id: serverId,
    set_as_default: setAsDefault 
  }, { withCredentials: true }),
  getSelected: () => api.get('/servers/select', { withCredentials: true })
}

export const authAPI = {
  status: () => api.get('/auth/status'),
  login: (username, password, totpCode = '') => api.post('/auth/login', {
    username,
    password,
    totp_code: totpCode
  }),
  logout: () => api.post('/auth/logout')
}

// Health check
export const healthCheck = () => api.get('/health')

export default api
