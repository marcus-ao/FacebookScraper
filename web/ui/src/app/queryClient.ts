import { QueryClient } from '@tanstack/react-query'

/**
 * 全局 Query 配置。
 *
 * ⚠️ Query **只管服务端状态**。编辑草稿绝不进缓存——它是运营已经做过的人工
 * 劳动，放进缓存会被 invalidate 抹掉（web/DESIGN.md：源文或提示词变化时
 * "过期，保留人工劳动"）。
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // 内网工具，数据要新；不做乐观缓存。
        staleTime: 0,
        gcTime: 5 * 60 * 1000,
        // 她会在编辑中切窗口去看飞书，回来不能被重拉打断。
        refetchOnWindowFocus: false,
        // 这里的失败大多是 409（版本冲突）或 400（输入不合法），重试只会重复失败；
        // 而月历刷新这类重试有外部副作用。5xx 由人点"重试"。
        retry: 0,
        // 错误进界面（就地显示 + 恢复入口），不进 error boundary。
        throwOnError: false,
      },
      mutations: {
        retry: 0,
      },
    },
  })
}
