import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Build output goes straight into the folder FastAPI serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    port: 5173,
    // `npm run dev` gives hot reload while the Python API stays on :8000.
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
