import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { TaskDetail } from '@/types/domain'
import type { ApprovalController } from '@/hooks/useApproval'
import { ApiError } from '@/services/http'
import fixture from '@/types/__fixtures__/task-detail-active.json'
import { DecisionPanel } from './DecisionPanel'

vi.mock('@/hooks/useCalendar', () => ({ useCalendar: () => ({ data: null }) }))

function render(overrides: Partial<ApprovalController> = {}, scheduled = false) {
  const at = '2026-09-30T23:00:00+08:00'
  const detail = { ...fixture, status: scheduled ? 'scheduled' : 'approved',
    publication: { status: scheduled ? 'scheduled' : 'submitted_unverified', scheduled_at: at },
    schedule: scheduled ? { at, channel: 'facebook' } : null } as unknown as TaskDetail
  const controller = { options: { data: { available: true, earliest: '2026-09-25T12:00:00+08:00', latest: at } },
    operation: { status: 'uncertain', message: 'old failed readback' }, pendingReceipt: !scheduled,
    reason: '请先恢复审核并准备好内容', suggestions: [], ...overrides } as unknown as ApprovalController
  return renderToStaticMarkup(<DecisionPanel detail={detail} controller={controller} editing={false} />)
}

describe('existing submission receipt', () => {
  it('shows the submitted time and success signal without suggesting another schedule', () => {
    const html = render()
    expect(html).toContain('已收到排期成功信号，待补齐回执')
    expect(html).toContain('2026-09-30T23:00:00+08:00')
    expect(html).toContain('23:00')
    expect(html).not.toContain('请先恢复审核')
    expect(html).not.toContain('可选 ')
    expect(html).toContain('不会再次提交')
  })
  it('keeps recovery failures distinct from a changed content revision', () => {
    const html = render({ error: new ApiError('发布浏览器未启动', 409, {}),
      recoveryError: true, errorMessage: '发布浏览器未启动' })
    expect(html).toContain('发布浏览器未启动')
    expect(html).not.toContain('状态在别处变过')
    expect(html).not.toContain('内容或时刻已变化')
  })
  it('lets the confirmed journal override a stale uncertain operation', () => {
    const html = render({}, true)
    expect(html).toContain('排期已确认')
    expect(html).not.toContain('old failed readback')
    expect(html).not.toContain('这次提交结果不明确')
  })
})
