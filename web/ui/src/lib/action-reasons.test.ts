import { describe, expect, it } from 'vitest'

import {
  approvalDisabledReason,
  initialTranslationDisabledReason,
  refinementDisabledReason,
  seedScheduleTime,
} from './action-reasons'


const approvalOk = {
  editing: false, busy: false, fetching: false, eligible: true,
  status: 'pending_review', optionsFailed: false, available: true, when: '2026-09-15T10:30',
} as const

describe('通过并创建排期：七个条件按她能动手的先后排', () => {
  it('全都满足时没有原因，按钮是亮的', () => {
    expect(approvalDisabledReason(approvalOk)).toBe('')
  })

  it.each(['T10:00', '2026-09-15', '2026-09-15T', '2026-02-29T10:00',
    '2026-04-31T10:00', '2026-00-15T10:00', '2026-09-15T24:00',
    '2026-09-15T10:60', '2026-09-15T10:00+02:00'])('不完整或非法的柏林时刻不能批准：%s', when => {
    expect(approvalDisabledReason({ ...approvalOk, when })).toBe('请填写完整有效的柏林日期和时间')
  })

  it('允许有效闰日，柏林夏令时语义继续由后端核对', () => {
    expect(approvalDisabledReason({ ...approvalOk, when: '2028-02-29T10:00' })).toBe('')
    expect(approvalDisabledReason({ ...approvalOk, when: '2026-10-25T02:30' })).toBe('')
  })

  it.each([
    [{ editing: true }, '请先保存或放弃正在编辑的文案'],
    [{ busy: true }, '正在提交并核验，请等待'],
    [{ fetching: true }, '正在核对发布条件'],
    [{ eligible: false }, '请先恢复审校并准备好内容'],
    [{ eligible: false, status: 'scheduled' as const }, '这篇已有已确认的排期'],
    [{ optionsFailed: true }, '发布条件读取失败，请重新核对'],
    [{ available: false }, '发布条件尚未满足，请查看下方提示'],
    [{ when: '' }, '请先填写柏林发布时间'],
  ])('%o → %s', (patch, expected) => {
    expect(approvalDisabledReason({ ...approvalOk, ...patch })).toBe(expected)
  })

  it('正在编辑压过其它所有条件 —— 先把编辑区收掉才谈得上排期', () => {
    expect(approvalDisabledReason({
      ...approvalOk, editing: true, busy: true, fetching: true,
      eligible: false, optionsFailed: true, available: false, when: '',
    })).toBe('请先保存或放弃正在编辑的文案')
  })

  it('已排期的那篇说的是"已有排期"，不是"请先恢复审校"', () => {
    expect(approvalDisabledReason({ ...approvalOk, eligible: false, status: 'scheduled' }))
      .toBe('这篇已有已确认的排期')
    expect(approvalDisabledReason({ ...approvalOk, eligible: false, status: 'snoozed' }))
      .toBe('请先恢复审校并准备好内容')
  })
})

const initialOk = {
  editing: false, busy: false, running: false, interrupted: false, available: true, consented: true,
} as const

describe('初翻：授权那一条排在最后，前面还有别的事要先做', () => {
  it('全都满足时可点', () => {
    expect(initialTranslationDisabledReason(initialOk)).toBe('')
  })

  it.each([
    [{ editing: true }, '请先保存或放弃当前编辑'],
    [{ busy: true }, '正在处理，请等待'],
    [{ running: true }, '当前初翻仍在处理'],
    [{ interrupted: true }, '请先核对中断的处理'],
    [{ available: false }, '当前无法开始初翻，请刷新处理状态'],
    [{ consented: false }, '请先确认可以处理这篇第三方内容'],
  ])('%o → %s', (patch, expected) => {
    expect(initialTranslationDisabledReason({ ...initialOk, ...patch })).toBe(expected)
  })

  it('还在跑的时候不提授权 —— 那是下一轮才需要的', () => {
    expect(initialTranslationDisabledReason({ ...initialOk, running: true, consented: false }))
      .toBe('当前初翻仍在处理')
  })
})

const refineOk = {
  editing: false, busy: false, eligible: true, running: false, interrupted: false,
  instruction: '保持事实，缩短开头', capabilitiesLoaded: true, kind: 'text', remaining: 3,
} as const

describe('单篇优化：七条原因，次数用完排在最后', () => {
  it('全都满足时可点', () => {
    expect(refinementDisabledReason(refineOk)).toBe('')
  })

  it.each([
    [{ editing: true }, '请先保存或放弃当前编辑'],
    [{ busy: true }, '正在处理，请等待'],
    [{ eligible: false }, '请先恢复审校并复核原文变化'],
    [{ running: true }, '当前优化仍在生成'],
    [{ interrupted: true }, '请先核对中断的处理'],
    [{ instruction: '   ' }, '请填写本次希望怎样调整'],
    [{ capabilitiesLoaded: false }, '正在读取可用次数与费用'],
    [{ kind: 'image' as const, remaining: 0 }, '这张图片的优化次数已用完'],
  ])('%o → %s', (patch, expected) => {
    expect(refinementDisabledReason({ ...refineOk, ...patch })).toBe(expected)
  })

  it('次数用完只管图片：文案优化不按张算', () => {
    expect(refinementDisabledReason({ ...refineOk, kind: 'text', remaining: 0 })).toBe('')
  })

  it('只有空白的指令等于没填', () => {
    expect(refinementDisabledReason({ ...refineOk, instruction: '\n \t' }))
      .toBe('请填写本次希望怎样调整')
  })
})

describe('默认排期时刻：她清空之后就不要再替她填回去', () => {
  const earliest = '2026-09-14T08:00:00+02:00'

  it('1. 初次加载：没人碰过、当前为空、后端给了范围 → 填默认值', () => {
    expect(seedScheduleTime({ current: '', touched: false, earliest, scheduledAt: null }))
      .toBe(earliest)
  })

  it('已有排期时用排期那一刻，不是最早可选时刻', () => {
    expect(seedScheduleTime({ current: '', touched: false, earliest, scheduledAt: '2026-09-20T17:00:00+02:00' }))
      .toBe('2026-09-20T17:00:00+02:00')
  })

  it('2–4. 她清空之后，approval-options 再回来也不许填回去', () => {
    expect(seedScheduleTime({ current: '', touched: true, earliest, scheduledAt: null })).toBeNull()
  })

  it('5–6. 换一篇任务：touched 重置，新的一篇照样有默认值', () => {
    expect(seedScheduleTime({ current: '', touched: false, earliest, scheduledAt: null }))
      .toBe(earliest)
  })

  it('已经填着东西就不动它', () => {
    expect(seedScheduleTime({ current: '2026-09-18T09:00', touched: false, earliest, scheduledAt: null }))
      .toBeNull()
  })

  it('后端还没给出可选范围时什么都不填', () => {
    expect(seedScheduleTime({ current: '', touched: false, earliest: null, scheduledAt: null }))
      .toBeNull()
    expect(seedScheduleTime({ current: '', touched: false, earliest: undefined, scheduledAt: null }))
      .toBeNull()
  })
})
