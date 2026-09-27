import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/queryClient'
import type { TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import { ApiError } from '@/services/http'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { ApprovalAction, DecisionPanel } from './DecisionPanel'

const detail = fixture as unknown as TaskDetail
const controller = {
  options: { data: null, isFetching: false }, eligible: false, lockable: false, locked: false,
  busy: false, reason: '请先确认正文', error: null, errorMessage: '', suggestions: [],
  confirmed: false, snapshot: null, operation: null, when: '', pendingReceipt: false,
  recovering: false, recoveryNotice: '', recoveryError: false,
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
  it('提交进度与失败只给业务状态，不透出底层浏览器步骤和异常', () => {
    const running = { ...controller, operation: { status: 'running', step_index: 2, step_total: 7,
      step: '新开标签页进 composer', message: '' } }
    const inProgress = html(detail, running as ApprovalController)
    expect(inProgress).toContain('正在创建排期')
    expect(inProgress).not.toContain('composer')
    const failed = { ...controller, operation: { status: 'failed', step_index: 2, step_total: 7,
      step: 'Planner 回读', message: 'remote DOM selector failed' } }
    const result = html(detail, failed as ApprovalController)
    expect(result).toContain('请核对这次提交尝试的结果')
    expect(result).not.toContain('Planner')
    expect(result).not.toContain('remote DOM')
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
  it('旧冻结帖缺人工确认时说明解除冻结重审，而不是指向不存在的下方提示', () => {
    const legacy = { ...controller, locked: true, eligible: true, reason: '发布条件尚未满足，请查看下方提示',
      options: { data: { business_timezone: 'Asia/Shanghai', available: false, lockable: false,
        reason: '请解除冻结、重新核对并确认当前正文和每张图片，然后再次冻结',
        preview: { target: { account: 'Neakasa Deutschland', channel: 'facebook' }, text: 'Frozen' } } } }
    const markup = html({ ...detail, status: 'content_locked' }, legacy as ApprovalController)
    expect(markup).toContain('请解除冻结、重新核对并确认当前正文和每张图片')
    expect(markup).not.toContain('请查看下方提示')
  })
})

describe('已有提交的回执', () => {
  const at = '2026-09-30T23:00:00+08:00'
  const receipt = (scheduled: boolean, overrides: Partial<ApprovalController> = {}) => html(
    { ...detail, status: scheduled ? 'scheduled' : 'approved',
      publication: { status: scheduled ? 'scheduled' : 'submitted_unverified', scheduled_at: at },
      schedule: scheduled ? { at, channel: 'facebook' } : null } as unknown as TaskDetail,
    { ...controller, options: { data: { available: true, earliest: '2026-09-25T12:00:00+08:00', latest: at }, isFetching: false },
      operation: { status: 'uncertain', message: 'old failed readback' }, pendingReceipt: !scheduled,
      reason: '请先核对已有提交结果', ...overrides } as unknown as ApprovalController)

  it('收到成功信号但回执未补齐：给提交时刻，只引导核对，不给可选范围', () => {
    const markup = receipt(false)
    expect(markup).toContain('已收到排期成功信号，待补齐回执')
    expect(markup).toContain(at)
    expect(markup).toContain('23:00')
    expect(markup).toContain('不会再次提交')
    expect(markup).not.toContain('可选时间')
    expect(markup).not.toContain('old failed readback')
  })
  it('核对失败与内容变化分开说明，不弹出「状态在别处变过」的恢复', () => {
    const markup = receipt(false, { error: new ApiError('发布浏览器未启动', 409, {}),
      recoveryError: true, errorMessage: '发布浏览器未启动' })
    expect(markup).toContain('发布浏览器未启动')
    expect(markup).not.toContain('状态在别处变过')
    expect(markup).not.toContain('内容或时刻已变化')
  })
  it('账本已确认排期时，旧的待核对操作不再显示', () => {
    const markup = receipt(true)
    expect(markup).toContain('定时排期已确认')
    expect(markup).not.toContain('提交结果待核对')
    expect(markup).not.toContain('old failed readback')
  })
  it('排期已确认但通知未送达时，另行说明通知状态', () => {
    const markup = receipt(true, { operation: { status: 'succeeded', result: {
      projection: { notification_notice: '排期已确认，飞书通知的送达结果不明确；请先到群里核对。' },
    } } as unknown as ApprovalController['operation'] })
    expect(markup).toContain('定时排期已确认')
    expect(markup).toContain('飞书通知的送达结果不明确')
    expect(markup).not.toContain('提交结果待核对')
  })
})
