import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物直接由 FastAPI 伺服（web/api/app.py 把 web/ui/dist 挂在 /）。
// 所以 base 用 '/'，assets 走绝对路径。
//
// ⚠️ 开发时前端跑在 5173，后端跑在 8765，/api 由下面这条 proxy 转过去；
// **构建之后两者同源，proxy 不参与**——生产机上只有 FastAPI 一个进程。
export default defineConfig({
  base: '/',
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 内网工具，出问题要能看堆栈；体积不是约束。
    sourcemap: true
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true }
    }
  }
})
