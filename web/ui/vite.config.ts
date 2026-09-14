import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// FastAPI 在 / 提供 dist 静态文件。开发服务使用 5174，将 /api 转到 8765。
// 构建后页面和 API 同源，运行静态服务不需要 Node。
export default defineConfig({
  base: '/',
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 保留源码映射，便于定位浏览器错误。
    sourcemap: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
})
