import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时将 /api 代理到 FastAPI；构建后由 FastAPI 同源提供页面。
export default defineConfig(({ mode }) => {
  const runtimeId = loadEnv(mode, process.cwd(), 'VITE_').VITE_FBSCRAPER_RUNTIME_ID || ''
  if (runtimeId && !/^[a-f0-9]{64}$/.test(runtimeId)) throw new Error('Invalid VITE_FBSCRAPER_RUNTIME_ID')
  return {
  base: '/',
  plugins: [react(), {
    name: 'deployment-runtime',
    generateBundle() {
      if (runtimeId) this.emitFile({ type: 'asset', fileName: 'runtime.json', source: JSON.stringify({ runtime_id: runtimeId }) })
    },
  }],
  define: { 'import.meta.env.VITE_FBSCRAPER_RUNTIME_ID': JSON.stringify(runtimeId) },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
  }
})
