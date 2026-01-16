import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Pobierz port backendu z zmiennej środowiskowej lub użyj domyślnego 5000
const backendPort = process.env.VITE_BACKEND_PORT || process.env.BACKEND_PORT || '5000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // Nasłuchuj na wszystkich interfejsach
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

