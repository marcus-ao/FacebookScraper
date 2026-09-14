import { describe, expect, it } from 'vitest'
import { runtimeSummary, stageSummary } from './model'
import type { RuntimeStage } from '@/types/domain'

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
