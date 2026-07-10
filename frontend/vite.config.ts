import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 代理目标按运行位置切换：宿主机跑 vite = localhost:8000（默认）；
// 容器里跑（compose dev profile）由 VITE_API_PROXY_TARGET=http://api:8000 注入
// ——容器内 localhost 是容器自己，写死会把登录请求转发进空端口（部署实战踩坑）。
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': '/src' },
  },
  server: {
    port: 5173,
    proxy: {
      // 本地开发直连后端；生产由 nginx 统一反代
      '/api': apiTarget,
      '/portal/v1': apiTarget,
    },
  },
})
