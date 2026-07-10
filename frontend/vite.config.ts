import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': '/src' },
  },
  server: {
    port: 5173,
    proxy: {
      // 本地开发直连后端容器；生产由 nginx 统一反代
      '/api': 'http://localhost:8000',
      '/portal/v1': 'http://localhost:8000',
    },
  },
})
