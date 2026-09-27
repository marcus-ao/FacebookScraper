import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClientProvider } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'

import { createQueryClient } from '@/app/queryClient'
import { routes } from '@/app/router'
import { parseHistoryListQuery } from '@/app/search-params'
import { historyListOptions, reviewListOptions } from '@/hooks/useTasks'
import { ApiError } from '@/services/http'

function failed(client: QueryClient, options: { queryKey: readonly unknown[] }, message: string) {
  // 挂载时默认会重试失败的查询，首屏就变回加载中；这里要看的是重试之前业务看到的那一屏。
  client.setQueryDefaults(['tasks'], { retryOnMount: false })
  client.getQueryCache().build(client, options).setState({
    status: 'error', fetchStatus: 'idle', error: new ApiError(message, 500, null) })
}

function render(path: string, client: QueryClient): string {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return renderToStaticMarkup(<ConfigProvider locale={zhCN}>
    <QueryClientProvider client={client}><AntdApp><RouterProvider router={router} /></AntdApp></QueryClientProvider>
  </ConfigProvider>)
}

describe('列表读取失败只给业务说法', () => {
  const raw = 'OperationalError: database is locked (state/index.sqlite)'

  it('历史归档不透出数据库异常原文', () => {
    const client = createQueryClient()
    failed(client, historyListOptions(parseHistoryListQuery(new URLSearchParams())), raw)
    const markup = render('/history', client)
    expect(markup).toContain('暂时无法读取历史归档')
    expect(markup).toContain('请点重试；仍无法读取请联系维护人员。')
    expect(markup).not.toContain('OperationalError')
    expect(markup).not.toContain('index.sqlite')
  })

  it('待审队列同样换成通用说明；中文业务原因原样保留', () => {
    const client = createQueryClient()
    failed(client, reviewListOptions(), raw)
    expect(render('/review/facebook', client)).not.toContain('OperationalError')
    const plain = createQueryClient()
    failed(plain, reviewListOptions(), '系统正在协调更新，请稍后再试。')
    expect(render('/review/facebook', plain)).toContain('系统正在协调更新，请稍后再试。')
  })
})
