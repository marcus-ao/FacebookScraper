import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

// 测试配置和构建配置分开。
//
// 纯逻辑与服务端静态渲染测试都在 Node 环境运行，`.test.tsx` 也要收进来。
//
// ⚠️ **没有装 jsdom，也没有装 @testing-library/react。** 组件测试用
// `react-dom/server` 的 renderToStaticMarkup 把组件渲染成 HTML 字符串再断言 ——
// react-dom 本来就在依赖里，这样一个新包都不用加。代价是 antd 的浮层
// （Tooltip / Popover 的弹出内容）走 portal，在 SSR 里不出现；那部分改为
// 因此浮层内容测试纯映射；交互由浏览器测试覆盖。
export default defineConfig({
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // 柏林/上海两套时刻语义必须在固定时区下验证，而且**故意不用这两个时区之一**。
    // 运行机器在中国（Asia/Shanghai），所以用 Asia/Shanghai 跑测试时，一个错误地
    // 依赖本地时区的"上海时刻"实现照样会通过。换成第三个时区，柏林和上海两套
    // 语义都必须靠自己算对，任何本地时区泄漏都会当场露出来。
    env: { TZ: 'America/New_York' },
  },
})
