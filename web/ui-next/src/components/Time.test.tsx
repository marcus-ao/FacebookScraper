import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { BerlinTime, ShanghaiTime } from './Time'

/**
 * ⚠️ 这一组跑在 `TZ=America/New_York` 下（vitest.config.ts 定的）。
 *
 * 故意不用柏林也不用上海：生产机在中国，用 `Asia/Shanghai` 跑测试时，
 * 一个错误地依赖本地时区的"上海时刻"实现照样会通过。换成第三个时区，
 * 两套语义都必须靠自己算对，任何本地时区泄漏当场暴露。
 */

const text = (node: React.ReactElement) =>
  renderToStaticMarkup(node).replace(/<[^>]+>/g, '').trim()

describe('BerlinTime：排期时刻不被本地时区改写', () => {
  it('夏令时 +02:00 的 17:00 就显示 17:00', () => {
    // 本地时区是纽约（UTC-4），如果走 toLocaleString 会显示 11:00。
    expect(text(<BerlinTime at="2026-09-13T17:00:00+02:00" />)).toBe('9/13 周日 17:00 柏林')
  })

  it('冬令时 +01:00 的 10:00 就显示 10:00', () => {
    expect(text(<BerlinTime at="2026-01-05T10:00:00+01:00" />)).toBe('1/5 周一 10:00 柏林')
  })

  it('同一天里 +02:00 与 +01:00 都只看墙上时刻（DST 切换日 10/25）', () => {
    // 2026-10-25 是柏林从夏令时切回冬令时的那一天，同一天里有两种偏移。
    expect(text(<BerlinTime at="2026-10-25T02:30:00+02:00" />)).toBe('10/25 周日 02:30 柏林')
    expect(text(<BerlinTime at="2026-10-25T02:30:00+01:00" />)).toBe('10/25 周日 02:30 柏林')
  })

  it('春季切换日 3/29 同理', () => {
    expect(text(<BerlinTime at="2026-03-29T03:30:00+02:00" />)).toBe('3/29 周日 03:30 柏林')
  })

  it('UTC 字符串也按字面读，不换算', () => {
    expect(text(<BerlinTime at="2026-09-13T17:00:00Z" />)).toBe('9/13 周日 17:00 柏林')
  })

  it('时区词是格式的一部分', () => {
    // 界面上同时有柏林与上海两套时刻，不标出来就会被当成同一套。
    expect(text(<BerlinTime at="2026-09-13T17:00:00+02:00" />)).toContain('柏林')
  })

  it('没有排期显示占位，不是空白', () => {
    expect(text(<BerlinTime at={null} />)).toBe('—')
    expect(text(<BerlinTime at={undefined} />)).toBe('—')
    expect(text(<BerlinTime at="" />)).toBe('—')
    expect(text(<BerlinTime at={null} fallback="暂无建议时刻" />)).toBe('暂无建议时刻')
  })

  it('解析不了的字符串也走占位，不显示 NaN', () => {
    expect(text(<BerlinTime at="不是时间" />)).toBe('—')
  })

  it('渲染成 <time>，机器可读的值是原始 ISO', () => {
    const markup = renderToStaticMarkup(<BerlinTime at="2026-09-13T17:00:00+02:00" />)
    expect(markup).toContain('<time')
    // React 19 的 SSR 把 dateTime 原样打出来；HTML 属性名大小写不敏感，
    // 浏览器解析出来仍然是 datetime，所以这里不区分大小写地断言。
    expect(markup.toLowerCase()).toContain('datetime="2026-09-13t17:00:00+02:00"')
    expect(markup).toContain('data-zone="berlin"')
  })
})

describe('ShanghaiTime：明确按 Asia/Shanghai', () => {
  it('UTC 07:51 是上海 15:51，不是纽约的 03:51', () => {
    expect(text(<ShanghaiTime at="2026-09-13T07:51:04Z" />)).toBe('2026/09/13 15:51 上海')
  })

  it('跨日：UTC 20:00 在上海已经是第二天 04:00', () => {
    expect(text(<ShanghaiTime at="2026-09-13T20:00:00Z" />)).toBe('2026/09/14 04:00 上海')
  })

  it('上海没有夏令时，冬夏两个日期都是 UTC+8', () => {
    expect(text(<ShanghaiTime at="2026-01-15T00:00:00Z" />)).toBe('2026/01/15 08:00 上海')
    expect(text(<ShanghaiTime at="2026-07-15T00:00:00Z" />)).toBe('2026/07/15 08:00 上海')
  })

  it('showZone={false} 与旧 UI 的 formatTrailTime 逐字一致', () => {
    expect(text(<ShanghaiTime at="2026-09-13T07:51:04Z" showZone={false} />)).toBe(
      '2026/09/13 15:51',
    )
  })

  it('没有值显示占位', () => {
    expect(text(<ShanghaiTime at={null} />)).toBe('—')
    expect(text(<ShanghaiTime at="不是时间" />)).toBe('—')
  })

  it('渲染成 <time> 并标出时区', () => {
    const markup = renderToStaticMarkup(<ShanghaiTime at="2026-09-13T07:51:04Z" />)
    expect(markup).toContain('data-zone="shanghai"')
  })
})

describe('两套语义不会互相污染', () => {
  it('同一个瞬间，柏林与上海显示不同的小时', () => {
    const at = '2026-09-13T17:00:00+02:00' // = 15:00 UTC = 23:00 上海
    expect(text(<BerlinTime at={at} />)).toContain('17:00')
    expect(text(<ShanghaiTime at={at} />)).toContain('23:00')
  })
})
