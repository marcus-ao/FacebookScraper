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
import type { ApprovalOptions, TaskDetail } from '@/types/domain'
import detailFixture from '@/types/__fixtures__/task-detail-active.json'
import { deriveSteps } from './step-model'
import { ReviewStepNav, ReviewStepPanel, ReviewTodoSummary, SourceStrip } from './ReviewShellParts'

const detail = detailFixture as unknown as TaskDetail
const [account, postId] = detail.id.split('/') as [string, string]

function renderDetail(tab: string, storage?: Record<string, unknown>, currentDetail: TaskDetail = detail,
  options?: ApprovalOptions): string {
  const current = storage ? { ...currentDetail, storage } : currentDetail
  const client = createQueryClient()
  client.setQueryData(taskKey(current.id), current)
  if (options) {
    // 视为刚读到的条件；否则挂载时会重新读取，页面按「正在核对」处理。
    client.setQueryDefaults(['approval-options'], { staleTime: Infinity })
    client.setQueryData(['approval-options', current.id, current.text.source_text_sha256,
      current.text.human_revision, current.review.revision, current.localization.revision], options)
  }
  const router = createMemoryRouter(routes, {
    initialEntries: [`/review/${account}/${encodeURIComponent(postId)}?tab=${tab}`],
  })
  return renderToStaticMarkup(
    <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }}
      theme={{ token: antdToken, components: antdComponents, hashed: false }}>
      <QueryClientProvider client={client}><AntdApp><RouterProvider router={router} /></AntdApp></QueryClientProvider>
    </ConfigProvider>,
  )
}

const IMAGE_WORKSPACE = 'aria-label="图片对照"'
const TAG_WORKSPACE = 'aria-label="话题标签选择"'

describe('详情步骤按需挂载', () => {
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

  it('打开最终确认深链，不提前挂载图片和标签编辑区', () => {
    const markup = renderDetail('final')
    expect(markup).toContain('最终确认与排期')
    expect(markup).not.toContain(IMAGE_WORKSPACE)
    expect(markup).not.toContain(TAG_WORKSPACE)
  })

  it('已有机器正文但未确认时，在正文步骤提供独立确认动作', () => {
    const machine = { ...detail, localization: { ...detail.localization, body_de: 'Maschinenentwurf' } }
    expect(renderDetail('text', undefined, machine)).toContain('确认当前德语正文')
    const confirmed = { ...machine, content_review: { ...machine.content_review,
      body: { confirmed: true, confirmed_at: '2026-09-25T00:00:00Z', version: 'b'.repeat(64) } } }
    expect(renderDetail('text', undefined, confirmed)).toContain('当前正文已确认')
  })

  it('最终步骤给可回跳的审核事项，未满足冻结条件时不显示灰色冻结按钮', () => {
    const markup = renderDetail('final')
    expect(markup).toContain('发布前审核情况')
    expect(markup).toContain('去处理')
    // 每行按钮的可访问名称带上步骤名，读屏时分得清是哪一步；跳转箭头不念出来。
    expect(markup).toContain('aria-label="德语正文：去处理"')
    expect(markup).toContain('<span aria-hidden="true"> ↗</span>')
    expect(markup).toContain('查看完整发布文案')
    expect(markup).not.toContain('aria-label="编辑确认无误"')
  })

  it('前三步都完成但服务端不许冻结时，说明业务原因，不暴露命令或内部术语', () => {
    const ready: TaskDetail = { ...detail, status: 'pending_review',
      localization: { ...detail.localization, body_de: 'Geprüfter Text', hashtags_confirmed: true, links_confirmed: true,
        links: detail.localization.links.map(link => ({ ...link, target_url: 'https://de.example/p', confirmed: true })) },
      images: detail.images.map(image => ({ ...image, ready: true })),
      content_review: {
        body: { confirmed: true, confirmed_at: '2026-09-25T00:00:00Z', version: 'b'.repeat(64) },
        images: detail.images.map(image => ({ index: image.index, confirmed: true,
          confirmed_at: '2026-09-25T00:00:00Z', version: 'c'.repeat(64) })),
      } }
    const options: ApprovalOptions = { available: false, reason: '', fingerprint: 'f'.repeat(64), lockable: false,
      lock_reason: '帖子 fixture：金额硬闸未通过：$10\npython -m localize.images --account "fa_x"', preview: null,
      platform: 'facebook', business_timezone: 'Asia/Shanghai', audience_timezone: 'Europe/Berlin',
      audience_quiet_hours: [0, 7], default_times: ['10:00'], earliest: '2026-09-28T10:00:00+08:00',
      latest: '2026-09-30T23:00:00+08:00', ui_timezone: 'Asia/Shanghai' }
    const markup = renderDetail('final', undefined, ready, options)
    expect(markup).toContain('暂时不能冻结内容')
    expect(markup).toContain('金额与原帖不一致')
    for (const hidden of ['python', '硬闸', 'aria-label="编辑确认无误"']) expect(markup).not.toContain(hidden)
    expect(renderDetail('final', undefined, ready, { ...options, lockable: true, lock_reason: '' }))
      .not.toContain('暂时不能冻结内容')
  })

  it('冻结账号的历史帖说明只供查阅，而不是只少了编辑按钮', () => {
    const markup = renderDetail('text', undefined, { ...detail, read_only: true })
    expect(markup).toContain('冻结账号的历史归档 · 仅供查阅')
    expect(markup).not.toContain('>编辑德语<')
    expect(renderDetail('text')).not.toContain('仅供查阅')
  })

  it('归档、索引和云盘事实不进入业务审核画布', () => {
    const markup = renderDetail('text', {
      classified_by: 'manual', account_dir: 'fa_neakasaofficial', folder: 'posts/2026-09/S10/post',
      first_archived_at: '2026-09-14T08:20:00+00:00',
      local: { status: 'partial', saved_images: 1, expected_images: 2 },
      database: { status: 'stale', verified_at: null },
      feishu: { enabled: true, status: 'uncertain', counts: { pending: 0, completed: 0, uncertain: 1, blocked: 0 }, last_success_at: null, operations: [], incomplete_source: true, missing_media: [1, 2] },
      media: [],
    })
    expect(markup).not.toContain('归档位置')
    expect(markup).not.toContain('展示索引')
    expect(markup).not.toContain('飞书云盘')
    expect(markup).not.toContain('源内容待补齐')
  })

  it('访问过的步骤隐藏时保留草稿节点，未访问步骤不挂载', () => {
    expect(renderToStaticMarkup(<ReviewStepPanel id="text" active="images" opened><p>未保存草稿</p></ReviewStepPanel>))
      .toContain('未保存草稿')
    expect(renderToStaticMarkup(<ReviewStepPanel id="final" active="images" opened={false}><p>排期</p></ReviewStepPanel>))
      .not.toContain('排期')
  })
})

describe('审核页紧凑外壳', () => {
  it('四步状态有文字说明；待办默认最多三项，展开才展示全部', () => {
    const steps = deriveSteps(detail)
    const nav = renderToStaticMarkup(<ReviewStepNav steps={steps} active="text" onChange={() => {}} />)
    for (const label of ['德语正文', '逐张图片', '标签与链接', '最终确认与排期', '待处理']) expect(nav).toContain(label)
    const todos = [...steps.flatMap(step => step.todos), { step: 'final' as const, text: '第四项' }]
    const collapsed = renderToStaticMarkup(<ReviewTodoSummary todos={todos} expanded={false} onExpand={() => {}} onNavigate={() => {}} />)
    const expanded = renderToStaticMarkup(<ReviewTodoSummary todos={todos} expanded onExpand={() => {}} onNavigate={() => {}} />)
    expect(collapsed).not.toContain('第四项')
    expect(expanded).toContain('第四项')
  })

  it('来源区显示真实作者、来源发布时间和原帖链接，不泄露归档路径或发布目标', () => {
    const source = renderToStaticMarkup(<SourceStrip detail={{ ...detail, meta: {
      ...detail.meta, account: 'source.account', owner: 'third.party', author_kind: 'third_party',
      created_at: '2026-09-21T16:00:00Z', permalink: 'https://example.invalid/post',
    } }} />)
    expect(source).toContain('source.account')
    expect(source).toContain('third.party')
    expect(source).toContain('9/22')
    expect(source).toContain('https://example.invalid/post')
    expect(source).not.toContain('archive')
    expect(source).not.toContain('Neakasa Deutschland')
  })
})
