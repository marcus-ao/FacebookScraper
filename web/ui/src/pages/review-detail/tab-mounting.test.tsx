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

function renderDetail(tab: string, storage?: Record<string, unknown>): string {
  const current = storage ? { ...detail, storage } : detail
  const client = createQueryClient()
  client.setQueryData(taskKey(current.id), current)
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

  it('展示本地、索引和飞书各自的存储事实，不把待核对媒体当作完整', () => {
    const markup = renderDetail('text', {
      classified_by: 'manual', account_dir: 'fa_neakasaofficial', folder: 'posts/2026-09/S10/post',
      first_archived_at: '2026-09-14T08:20:00+00:00',
      local: { status: 'partial', saved_images: 1, expected_images: 2 },
      database: { status: 'stale', verified_at: null },
      feishu: { enabled: true, status: 'uncertain', counts: { pending: 0, completed: 0, uncertain: 1, blocked: 0 }, last_success_at: null, operations: [], incomplete_source: true, missing_media: [1, 2] },
      media: [],
    })
    expect(markup).toContain('归档位置')
    expect(markup).toContain('人工分类')
    expect(markup).toContain('本地媒体：已保存 1 张，历史记录未提供校验证据，原帖媒体列表待核对')
    expect(markup).toContain('展示索引：需要刷新后核对')
    expect(markup).toContain('飞书云盘：有 1 项结果待人工核对')
    expect(markup).toContain('源内容待补齐，缺少 2 个媒体')
  })
})
