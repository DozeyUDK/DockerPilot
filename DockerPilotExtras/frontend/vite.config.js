import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Read backend port from the environment or fall back to 5000
const backendPort = process.env.VITE_BACKEND_PORT || process.env.BACKEND_PORT || '5000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // Listen on all interfaces
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
})

