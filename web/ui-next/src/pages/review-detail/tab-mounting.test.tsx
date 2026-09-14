import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClientProvider } from '@tanstack/react-query'

import { createQueryClient } from '@/app/queryClient'
import { routes } from '@/app/router'
import { antdComponents, antdToken } from '@/app/theme'
import { taskKey } from '@/hooks/useTasks'
import type { TaskDetail } from '@/types/domain'
import detailFixture from '@/types/__fixtures__/task-detail-active.json'

/**
 * 详情三个标签页的**挂载时机**。
 *
 * 这是本轮复核修掉的一处：原实现把图片工作区常驻在 `hidden` 容器里，于是每打开
 * 一篇详情、哪怕运营只看正文，也会先把当前这张的原图和德语图各拉一次
 * （图片响应是 `Cache-Control: no-cache`，每次都是真实往返）；而话题标签页反过来
 * 用条件渲染，切一下正文就把已经付费生成的标签建议整块卸载掉。
 *
 * 这里只验第一次渲染时谁在 DOM 里 —— 这一条 SSR 就能答。"打开过之后要一直留着"
 * 需要真实交互，由 tests/review_probe.py 在 Playwright 里守。
 */
const detail = detailFixture as unknown as TaskDetail
const [account, postId] = detail.id.split('/') as [string, string]

function renderDetail(tab: string): string {
  const client = createQueryClient()
  client.setQueryData(taskKey(detail.id), detail)
  const router = createMemoryRouter(routes, {
    initialEntries: [`/review/${account}/${encodeURIComponent(postId)}?tab=${tab}`],
  })
  return renderToStaticMarkup(
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{ token: antdToken, components: antdComponents, hashed: false }}
    >
      <QueryClientProvider client={client}>
        <AntdApp>
          <RouterProvider router={router} />
        </AntdApp>
      </QueryClientProvider>
    </ConfigProvider>,
  )
}

const IMAGE_WORKSPACE = 'aria-label="图片对照"'
const TAG_WORKSPACE = 'aria-label="话题标签选择"'

describe('详情标签页按需挂载', () => {
  it('只看正文时不挂载图片工作区，也就不会提前请求原图/德语图', () => {
    const markup = renderDetail('text')
    expect(markup).toContain('aria-label="正文对照"')
    expect(markup).not.toContain(IMAGE_WORKSPACE)
    expect(markup).not.toContain(detail.images[0]!.original_url)
    expect(markup).not.toContain(detail.images[0]!.de_url)
  })

  it('只看正文时也不挂载话题标签页', () => {
    expect(renderDetail('text')).not.toContain(TAG_WORKSPACE)
  })

  it('打开图片页才挂载图片工作区', () => {
    expect(renderDetail('images')).toContain(IMAGE_WORKSPACE)
  })

  it('打开话题标签页才挂载标签与链接', () => {
    expect(renderDetail('localization')).toContain(TAG_WORKSPACE)
  })
})
