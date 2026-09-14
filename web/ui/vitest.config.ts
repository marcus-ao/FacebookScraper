import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

// Node 运行纯逻辑与 SSR 测试；浮层交互由浏览器测试覆盖。
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // 使用第三时区，检测对宿主时区的隐式依赖。
    env: { TZ: 'America/New_York' },
  },
})
