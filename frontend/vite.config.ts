import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/extract': { target: 'http://localhost:8001', changeOrigin: true },
      '/documents': { target: 'http://localhost:8001', changeOrigin: true },
      '/health': { target: 'http://localhost:8001', changeOrigin: true },
      '/docs': { target: 'http://localhost:8001', changeOrigin: true },
      '/openapi.json': { target: 'http://localhost:8001', changeOrigin: true },
    },
  },
})
