/*
 * Authors: Claire Liu, Yu-Jing Wei
 * Description: Vite build configuration for the Door-bell frontend, including dev-server proxy
 * rules for the Java backend WebSocket/REST API and MediaMTX WHEP signaling endpoints.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Java Backend WebSocket
      '/ws': {
        target: 'http://localhost:8080',
        ws: true,
      },
      // Java Backend REST API (for future use)
      '/api': {
        target: 'http://localhost:8080',
      },
      // MediaMTX WHEP (WebRTC signaling)
      '/cam-': {
        target: 'http://localhost:8889',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
