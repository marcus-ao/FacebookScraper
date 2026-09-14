import { describe, expect, it } from 'vitest'

import {
  ACTION_LABEL,
  AUTHOR_KIND_LABEL,
  PLATFORM_LABEL,
  RISK_KIND_LABEL,
  STATUS_LABEL,
  formatDate,
  formatSchedule,
  berlinToday,
  formatTrailTime,
  formatWakeAt,
  wallMinutesApart,
} from './format'

// 这份测试跑在 TZ=America/New_York 下（见 vitest.config.ts）：
// 柏林和上海都不是本地时区，所以任何"偷偷用了浏览器本地时区"的实现
// 都会在这里当场失败。这是业务后果最重的一组用例——
// 把 10:00 柏林显示成别的时刻，就是把帖子发错时间。

describe('formatSchedule：柏林时刻必须按字符串里带的偏移显示，不经本地时区', () => {
  it('夏令时期间（+02:00）显示字符串里的墙上时刻', () => {
    expect(formatSchedule('2026-07-01T10:00:00+02:00')).toBe('7/1 周三 10:00 柏林')
  })

  it('本地时区换算会给出别的小时 —— 证明上面那条不是巧合', () => {
    // 同一时刻在 America/New_York 是 04:00。实现一旦改成
    // new Date(iso).toLocaleString()，输出就会变成 04:00。
    const localHour = new Date('2026-07-01T10:00:00+02:00').getHours()
    expect(localHour).not.toBe(10)
    expect(formatSchedule('2026-07-01T10:00:00+02:00')).toContain('10:00')
  })

  it('冬令时期间（+01:00）同样按字符串显示', () => {
    expect(formatSchedule('2026-12-02T17:30:00+01:00')).toBe('12/2 周三 17:30 柏林')
  })

  it('UTC 后缀的字符串也按字面时分显示（不做偏移换算）', () => {
    // formatSchedule 是"显示字符串里写的墙上时刻"，不是"换算到柏林"。
    // 换算由后端负责；前端再换一次就会错两遍。
    expect(formatSchedule('2026-07-01T10:00:00Z')).toBe('7/1 周三 10:00 柏林')
  })

  describe('夏令时边界', () => {
    // 2026 年欧盟夏令时：3/29 开始，10/25 结束。两天都是周日。
    it('切换日当天（3/29，+02:00）', () => {
      expect(formatSchedule('2026-03-29T03:00:00+02:00')).toBe('3/29 周日 03:00 柏林')
    })

    it('切换日当天（10/25，+01:00）', () => {
      expect(formatSchedule('2026-10-25T02:30:00+01:00')).toBe('10/25 周日 02:30 柏林')
    })

    it('同一天里 +02:00 与 +01:00 都只看墙上时刻 —— 这正是不能换算的原因', () => {
      // 10/25 02:30 在柏林出现两次（一次 +02:00 一次 +01:00）。
      // 前端不许"聪明地"消歧；它只显示后端给的那一个。
      expect(formatSchedule('2026-10-25T02:30:00+02:00')).toBe('10/25 周日 02:30 柏林')
      expect(formatSchedule('2026-10-25T02:30:00+01:00')).toBe('10/25 周日 02:30 柏林')
    })
  })

  it('星期用 UTC 算，不受本地时区影响', () => {
    // 2026-09-13 是周日（与夹具截图上的「建议 9/13 周日 17:00 柏林」一致）。
    expect(formatSchedule('2026-09-13T17:00:00+02:00')).toBe('9/13 周日 17:00 柏林')
    // 午夜时刻最容易因本地时区退到前一天。
    expect(formatSchedule('2026-09-13T00:05:00+02:00')).toContain('周日')
    expect(formatSchedule('2026-09-13T23:55:00+02:00')).toContain('周日')
  })

  it('月与日不补零（9/1 不是 09/01）', () => {
    expect(formatSchedule('2026-09-01T08:00:00+02:00')).toBe('9/1 周二 08:00 柏林')
  })

  it('时与分补零', () => {
    expect(formatSchedule('2026-09-01T08:05:00+02:00')).toContain('08:05')
  })

  it('null / 空 / 格式不对时返回 null，由调用方显示「暂无建议时刻」', () => {
    expect(formatSchedule(null)).toBeNull()
    expect(formatSchedule(undefined)).toBeNull()
    expect(formatSchedule('')).toBeNull()
    expect(formatSchedule('not a date')).toBeNull()
    expect(formatSchedule('2026-09')).toBeNull()
  })
})

describe('formatTrailTime / formatWakeAt：操作记录是上海时刻', () => {
  it('UTC 02:00 在上海是同日 10:00', () => {
    const shown = formatTrailTime('2026-07-01T02:00:00Z')
    expect(shown).toContain('2026')
    expect(shown).toContain('10:00')
  })

  it('不是本地时区（New York 会是 22:00 前一天），也不是 UTC', () => {
    const shown = formatTrailTime('2026-07-01T02:00:00Z')
    expect(shown).not.toContain('22:00') // America/New_York
    expect(shown).not.toContain('02:00') // UTC
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

  it('formatWakeAt 在后面补「上海」', () => {
    expect(formatWakeAt('2026-07-01T02:00:00Z')).toMatch(/ 上海$/)
  })

  it('空值与非法值返回空串（不是 "Invalid Date"）', () => {
    expect(formatTrailTime(null)).toBe('')
    expect(formatTrailTime('')).toBe('')
    expect(formatTrailTime('nonsense')).toBe('')
    expect(formatWakeAt(null)).toBe('')
    expect(formatWakeAt('')).toBe('')
  })
})

describe('formatDate', () => {
  it('只取前十位', () => {
    expect(formatDate('2026-07-01T14:00:34Z')).toBe('2026-07-01')
  })

  it('空值给破折号', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate(undefined)).toBe('—')
    expect(formatDate('')).toBe('—')
  })
})

describe('文案表', () => {
  it('八个状态各有文案', () => {
    expect(Object.keys(STATUS_LABEL)).toHaveLength(8)
    expect(Object.values(STATUS_LABEL).every((value) => value.length > 0)).toBe(true)
  })

  it('not_ready 显示「未就绪」 —— DECISION_LOG 定的唯一一处文案改动', () => {
    expect(STATUS_LABEL.not_ready).toBe('未就绪')
    // 「待处理」会与「待我审」之外的分桶概念混淆。
    expect(STATUS_LABEL.not_ready).not.toBe('待处理')
  })

  it('其余七个状态使用既定文案', () => {
    expect(STATUS_LABEL.pending_review).toBe('待我审')
    expect(STATUS_LABEL.edited).toBe('已修改')
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
    expect(Object.keys(ACTION_LABEL)).toHaveLength(9)
    expect(ACTION_LABEL.text_edited).toBe('修改了德语译文')
  })
})

describe('berlinToday：月历的「今天」按柏林算，不按浏览器本地时区', () => {
  // 这组用例的价值全在"跑在 America/New_York 下"这件事上：下面每一个断言
  // 换成 new Date().getDate() 都会是另一个日期。
  it('柏林已经跨到第二天，纽约和上海都还没有', () => {
    // 2026-09-13T22:30Z：柏林 +02:00 → 09-14 00:30，纽约 -04:00 → 09-13 18:30。
    expect(berlinToday(new Date('2026-09-13T22:30:00Z'))).toBe('2026-09-14')
  })

  it('上海已经是第二天了，柏林还没有 —— 这台机器在中国', () => {
    // 2026-09-13T17:00Z：上海 +08:00 → 09-14 01:00，柏林 +02:00 → 09-13 19:00。
    expect(berlinToday(new Date('2026-09-13T17:00:00Z'))).toBe('2026-09-13')
  })

  it('冬令时按 +01:00 算，不是固定偏移', () => {
    // 2026-01-13T23:30Z：柏林 +01:00 → 01-14 00:30。
    expect(berlinToday(new Date('2026-01-13T23:30:00Z'))).toBe('2026-01-14')
    // 同一个墙上钟点，夏令时那天就该停在当天。
    expect(berlinToday(new Date('2026-07-13T22:30:00Z'))).toBe('2026-07-14')
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
