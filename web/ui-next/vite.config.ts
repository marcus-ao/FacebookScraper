import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 与 web/ui/vite.config.js 保持同一套部署假设：
// 构建产物由 FastAPI 伺服（web/api/app.py 按配置把 dist 挂在 /），所以 base 用 '/'。
//
// 开发时前端跑在 5174（旧应用占着 5173，迁移期两者要能同时起），
// 后端跑在 8765，/api 由下面这条 proxy 转过去；
// 构建之后两者同源，proxy 不参与 —— 生产机上只有 FastAPI 一个进程。
export default defineConfig({
  base: '/',
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 内网工具，出问题要能看堆栈；体积不是约束。与旧应用同口径。
    sourcemap: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
    },
  },
})
