import { describe, expect, it } from 'vitest'

import {
  ACTION_LABEL,
  AUTHOR_KIND_LABEL,
  PLATFORM_LABEL,
  RISK_KIND_LABEL,
  STATUS_LABEL,
  formatDate,
  formatSchedule,
  businessToday,
  formatTrailTime,
  wallMinutesApart,
} from './format'

// 使用第三时区运行，避免宿主时区掩盖错误。

describe('formatSchedule：带偏移的绝对时刻统一转换到业务时钟', () => {
  it('Z、+02:00 与 +08:00 同一瞬间显示相同钟点', () => {
    for (const iso of ['2026-09-24T14:30:00Z', '2026-09-24T16:30:00+02:00', '2026-09-24T22:30:00+08:00']) {
      expect(formatSchedule(iso)).toBe('9/24 周四 22:30')
    }
  })

  it('跨日时日期和星期一并转换', () => {
    expect(formatSchedule('2026-09-24T20:30:00Z')).toBe('9/25 周五 04:30')
    expect(formatSchedule('2026-12-02T17:30:00+01:00')).toBe('12/3 周四 00:30')
  })

  it('有夏令时偏移的两个不同瞬间显示为不同业务钟点', () => {
    expect(formatSchedule('2026-10-25T02:30:00+02:00')).toBe('10/25 周日 08:30')
    expect(formatSchedule('2026-10-25T02:30:00+01:00')).toBe('10/25 周日 09:30')
  })

  it('无偏移的 datetime-local 保留用户输入的墙上时刻', () => {
    expect(formatSchedule('2026-09-25T04:30')).toBe('9/25 周五 04:30')
  })

  it('null / 空 / 格式不对时返回 null，由调用方显示「暂无建议时刻」', () => {
    expect(formatSchedule(null)).toBeNull()
    expect(formatSchedule(undefined)).toBeNull()
    expect(formatSchedule('')).toBeNull()
    expect(formatSchedule('not a date')).toBeNull()
    expect(formatSchedule('2026-09')).toBeNull()
  })
})

describe('formatTrailTime：操作记录时刻', () => {
  it('UTC 02:00 在上海是同日 10:00', () => {
    const shown = formatTrailTime('2026-07-01T02:00:00Z')
    expect(shown).toContain('2026')
    expect(shown).toContain('10:00')
  })

  it('不是本地时区（New York 会是 22:00 前一天），也不是 UTC', () => {
    const shown = formatTrailTime('2026-07-01T02:00:00Z')
    expect(shown).not.toContain('22:00')
    expect(shown).not.toContain('02:00')
  })

  it('跨日：UTC 20:00 在上海是次日 04:00', () => {
    const shown = formatTrailTime('2026-06-30T20:00:00Z')
    expect(shown).toContain('04:00')
    expect(shown).toContain('07')
  })

  it('24 小时制，不出现 AM/PM', () => {
    const shown = formatTrailTime('2026-07-01T09:00:00Z')
    expect(shown).toContain('17:00')
    expect(shown).not.toMatch(/[AP]M/i)
  })

  it('空值与非法值返回空串（不是 "Invalid Date"）', () => {
    expect(formatTrailTime(null)).toBe('')
    expect(formatTrailTime('')).toBe('')
    expect(formatTrailTime('nonsense')).toBe('')
  })
})

describe('formatDate', () => {
  it('来源时间跨业务日期时按实际时刻换日，纯日期保持不变', () => {
    expect(formatDate('2026-09-24T20:30:00Z')).toBe('2026-09-25')
    expect(formatDate('2026-09-25')).toBe('2026-09-25')
  })

  it('空值给破折号', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate(undefined)).toBe('—')
    expect(formatDate('')).toBe('—')
  })
})

describe('文案表', () => {
  it('九个状态各有文案', () => {
    expect(Object.keys(STATUS_LABEL)).toHaveLength(9)
    expect(Object.values(STATUS_LABEL).every((value) => value.length > 0)).toBe(true)
  })

  it('not_ready 显示「未就绪」 —— DECISION_LOG 定的唯一一处文案改动', () => {
    expect(STATUS_LABEL.not_ready).toBe('未就绪')
    expect(STATUS_LABEL.not_ready).not.toBe('待处理')
  })

  it('其余八个状态使用既定文案', () => {
    expect(STATUS_LABEL.pending_review).toBe('待我审')
    expect(STATUS_LABEL.edited).toBe('已修改')
    expect(STATUS_LABEL.content_locked).toBe('内容已冻结')
    expect(STATUS_LABEL.snoozed).toBe('已挂起')
    expect(STATUS_LABEL.approved).toBe('已通过')
    expect(STATUS_LABEL.scheduled).toBe('已排期')
    expect(STATUS_LABEL.skipped).toBe('这篇不发')
    expect(STATUS_LABEL.handed_off).toBe('已交人工处理')
  })

  it('scheduled 的文案不能写成「已发布」 —— 它只表示排期被回读确认', () => {
    expect(STATUS_LABEL.scheduled).not.toContain('已发布')
  })

  it('平台、作者、风险、动作四张表都搬全了', () => {
    expect(PLATFORM_LABEL).toEqual({ facebook: 'Facebook', instagram: 'Instagram' })
    expect(Object.keys(AUTHOR_KIND_LABEL)).toHaveLength(3)
    expect(Object.keys(RISK_KIND_LABEL)).toHaveLength(3)
    expect(Object.keys(ACTION_LABEL)).toHaveLength(15)
    expect(ACTION_LABEL.text_edited).toBe('修改了德语译文')
    expect(ACTION_LABEL.body_reviewed).toBe('更新了正文核对结果')
    expect(ACTION_LABEL.image_reviewed).toBe('更新了图片核对结果')
  })
})

describe('businessToday：月历的「今天」按业务时区算，不按浏览器本地时区', () => {
  it('北京已经跨到第二天，UTC 和柏林都还没有', () => {
    expect(businessToday('Asia/Shanghai', new Date('2026-09-13T17:00:00Z'))).toBe('2026-09-14')
  })

  it('北京还没跨日', () => {
    expect(businessToday('Asia/Shanghai', new Date('2026-09-13T15:30:00Z'))).toBe('2026-09-13')
  })

  it('北京全年 +08:00，同一瞬间在一月和七月算出同一个偏移', () => {
    expect(businessToday('Asia/Shanghai', new Date('2026-01-13T16:30:00Z'))).toBe('2026-01-14')
    expect(businessToday('Asia/Shanghai', new Date('2026-07-13T16:30:00Z'))).toBe('2026-07-14')
  })

  it('换成有夏令时的业务时区仍然按当时的真实偏移算', () => {
    expect(businessToday('Europe/Berlin', new Date('2026-01-13T23:30:00Z'))).toBe('2026-01-14')
    expect(businessToday('Europe/Berlin', new Date('2026-07-13T22:30:00Z'))).toBe('2026-07-14')
  })
})

describe('wallMinutesApart：跨午夜也是真实分钟差', () => {
  it('23:30 与次日 00:30 是 60 分钟，不是"不同的两天"', () => {
    expect(wallMinutesApart('2026-09-14T00:30', '2026-09-13T23:30:00+02:00')).toBe(60)
  })

  it('两边顺序无关', () => {
    expect(wallMinutesApart('2026-09-13T23:30', '2026-09-14T00:30:00+02:00')).toBe(60)
  })
})
