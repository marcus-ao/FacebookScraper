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

/** SSR 检查首次挂载；打开后保留内容由浏览器测试覆盖。 */
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
