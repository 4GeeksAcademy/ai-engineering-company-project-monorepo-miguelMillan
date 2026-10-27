import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    plugins: [react(), tailwindcss()],
    server: {
      host: '0.0.0.0',
      port: 5174,
      proxy: {
        '/auth': {
          target: process.env.AUTH_PROXY_TARGET ?? 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
        '/users': {
          target: process.env.AUTH_PROXY_TARGET ?? 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
        '/profiles': {
          target: process.env.AUTH_PROXY_TARGET ?? 'http://127.0.0.1:8081',
          changeOrigin: true,
        },
        '/suppliers': {
          target: process.env.API_PROXY_TARGET ?? 'http://localhost:8080',
          changeOrigin: true,
        },
        '/api': {
          target: process.env.API_PROXY_TARGET ?? 'http://localhost:8080',
          changeOrigin: true,
        },
        '/inventory': {
          target: env.NEXT_PUBLIC_INVENTORY_API_URL ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
