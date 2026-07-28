import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Toutes les requêtes /api/* du frontend dev sont proxifiées vers le
      // backend FastAPI sur :8765. Évite la config CORS côté backend.
      // ws: false : on n'a pas de WebSockets ; les SSE (text/event-stream)
      // sont du HTTP standard, le proxy les gère sans config particulière.
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        ws: false,
      },
    },
  },
})
