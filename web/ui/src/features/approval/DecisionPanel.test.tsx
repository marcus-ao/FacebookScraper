import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/queryClient'
import type { TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { ApprovalAction, DecisionPanel } from './DecisionPanel'

const detail = fixture as unknown as TaskDetail
const controller = {
  options: { data: null, isFetching: false }, eligible: false, lockable: false, locked: false,
  busy: false, reason: '请先确认正文', error: null, errorMessage: '', suggestions: [],
  confirmed: false, snapshot: null, operation: null, when: '',
  setWhen: () => {}, refresh: async () => {}, recover: async () => {},
} as unknown as ApprovalController

const html = (post = detail, approval = controller, calendar?: unknown) => {
  const client = createQueryClient()
  if (calendar) client.setQueryData(['calendar'], calendar)
  return renderToStaticMarkup(<QueryClientProvider client={client}><DecisionPanel detail={post} controller={approval} editing={false} /></QueryClientProvider>)
}

describe('最终审核区', () => {
  it('未完成正文或图片时给可操作待办，不留灰色冻结主按钮', () => {
    expect(renderToStaticMarkup(<ApprovalAction controller={controller} />)).not.toContain('编辑确认无误')
    expect(html()).toContain('查看完整发布文案')
    expect(html()).not.toContain('作者：')
    expect(html()).not.toContain('依据缓存')
  })
  it('不确定提交只能核对已有尝试，不鼓励重复提交', () => {
    const uncertain = { ...controller, operation: { status: 'uncertain', message: 'unknown' } }
    const markup = html(detail, uncertain as ApprovalController)
    expect(markup).toContain('提交结果待核对')
    expect(markup).toContain('核对并补齐本地回执')
    expect(markup).not.toContain('<span>再次提交</span>')
  })
  it('已排期说明定时任务已确认，公开发布仍待观测', () => {
    const markup = html({ ...detail, status: 'scheduled' }, controller)
    expect(markup).toContain('定时排期已确认')
    expect(markup).toContain('公开发布仍待观测')
  })
  it('选时只展示附近同渠道冲突和可选范围，不展示缓存诊断或全天占用', () => {
    const ready = { ...controller, locked: true, eligible: true, when: '2026-09-30T17:30', options: { data: {
      business_timezone: 'Asia/Shanghai', earliest: '2026-09-24T08:00:00+08:00', latest: '2026-10-28T23:00:00+08:00',
      available: true, preview: { target: { account: 'Neakasa Deutschland', channel: 'facebook' }, text: 'Frozen' },
    } } }
    const markup = html({ ...detail, status: 'content_locked' }, ready as ApprovalController, {
      gap_minutes: 90, stale: false, cached_at: '2026-09-30T16:00:00+08:00', cards: [
        { at_business: '2026-09-30T17:31:00+08:00', channels: ['facebook'], delivery: 'scheduled' },
        { at_business: '2026-09-30T17:30:00+08:00', channels: ['instagram'], delivery: 'scheduled' },
      ],
    })
    expect(markup).toContain('所选时刻附近已有同渠道排期')
    expect(markup).toContain('可选时间')
    expect(markup).toContain('Neakasa Deutschland')
    expect(markup).not.toContain('依据缓存')
    expect(markup).not.toContain('前后一天')
  })
})
