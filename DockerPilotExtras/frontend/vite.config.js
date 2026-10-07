import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Read backend port from the environment or fall back to 5000
const backendPort = process.env.VITE_BACKEND_PORT || process.env.BACKEND_PORT || '5000'
const devHost = process.env.VITE_HOST || '127.0.0.1'
const webAuthEnabled = (process.env.WEB_AUTH_ENABLED || 'false').toLowerCase() === 'true'

if (!['127.0.0.1', 'localhost', '::1'].includes(devHost) && !webAuthEnabled) {
  throw new Error(
    'Refusing non-loopback Vite dev bind without WEB_AUTH_ENABLED=true'
  )
}

export default defineConfig(({ command }) => {
  // Vite CLI --host overrides server.host after config evaluation.
  // Reject any CLI host override for an unauthenticated dev server.
  if (!webAuthEnabled && command === 'serve' &&
      process.argv.some(arg => arg === '--host' || arg.startsWith('--host='))) {
    throw new Error('Refusing Vite --host override without WEB_AUTH_ENABLED=true')
  }

  return {
  plugins: [react()],
  server: {
    host: devHost,
    port: 3000,
    allowedHosts: [
      'localhost',
      '.localhost',
      'dozeyserver',
      '.dozeyserver'
    ],
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'build'
  }
  }
})
