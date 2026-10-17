import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // One bundle served from localhost; splitting would buy nothing, so no 500 kB warning.
  build: { outDir: '../aiteams/static', emptyOutDir: true, chunkSizeWarningLimit: 2000 },
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:7700', changeOrigin: true },
      '/auth': { target: 'http://127.0.0.1:7700', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:7700', ws: true },
    },
  },
})
