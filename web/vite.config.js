import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Vite dev server proxies /api/* to the FastAPI backend so the React
// app can call relative URLs (no CORS friction in development).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
