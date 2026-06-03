import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The React PWA is mounted at /app/ so the public landing page can live at /.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
})
