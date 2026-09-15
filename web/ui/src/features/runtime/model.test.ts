import { describe, expect, it } from 'vitest'
import { mirrorDiagnostics, mirrorSummary, runtimeSummary, stageSummary } from './model'
import type { MirrorStatus, RuntimeStage } from '@/types/domain'

describe('五阶段状态的保守表达', () => {
  it.each(['not_observed','new_backend_state','state_unreadable','disabled'])('未知或不可读 %s 不能显示成功', status => {
    expect(stageSummary({ number: 3, name: '德语本地化', status }).tone).not.toBe('success')
  })
  it('进程活跃不代替业务成功', () => {
    expect(stageSummary({ number: 1, name: '监测', status: 'active' })).toEqual({ tone: 'processing', label: '进程活跃', conclusion: '已观测到调度进程，业务结果请查看各阶段。' })
  })
  it('只接受与具体阶段相符的已知成功状态', () => {
    const wrong: RuntimeStage = { number: 3, name: '德语本地化', status: 'available' }
    expect(stageSummary(wrong).tone).toBe('default')
    expect(stageSummary({ ...wrong, status: 'completed' }).tone).toBe('success')
  })
  it('没有快照显示未确认', () => { expect(runtimeSummary(undefined)).toEqual({ tone: 'default', label: '未确认' }) })
})

describe('云盘状态的保守说明', () => {
  const status = (value: string, counts: Partial<MirrorStatus['counts']> = {}): MirrorStatus => ({
    enabled: value !== 'disabled', status: value,
    counts: { pending: 0, completed: 0, uncertain: 0, blocked: 0, ...counts },
    last_success_at: null, operations: [],
  })

  it('等待、待核对和暂停都说明原因，不把目录或配置当成上传完成', () => {
    expect(mirrorSummary(status('disabled')).label).toBe('未启用')
    expect(mirrorSummary(status('pending', { pending: 2 })).conclusion).toContain('2 项')
    expect(mirrorSummary(status('uncertain', { uncertain: 1 })).conclusion).toContain('人工核对')
    expect(mirrorSummary(status('blocked', { blocked: 3 })).conclusion).toContain('维护人员')
  })

  it.each(['idle', 'completed', 'new_provider_value'])('未知或只读汇总 %s 不暗示实时云端结果', value => {
    const summary = mirrorSummary(status(value))
    expect(summary.conclusion).not.toContain('实时')
    if (value === 'new_provider_value') expect(summary.tone).toBe('default')
  })

  it('维护详情只保留待核对的操作编号，不暴露云端令牌或实现字段', () => {
    const detail = mirrorDiagnostics({ ...status('uncertain', { uncertain: 1 }), operations: [
      { id: 'support-1', kind: 'upload', status: 'uncertain', sha256: 'private-digest', remote_token: 'private-token', error: { reason: 'private' } },
      { id: 'done-2', kind: 'upload', status: 'completed', sha256: 'digest', remote_token: 'token' },
    ] })
    expect(detail.operations).toEqual([{ id: 'support-1', status: 'uncertain' }])
  })
})
