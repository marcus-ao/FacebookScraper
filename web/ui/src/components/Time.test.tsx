import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { BusinessTime, ShanghaiTime } from './Time'

/** 在第三时区运行，检测对宿主时区的隐式依赖。 */

const text = (node: React.ReactElement) =>
  renderToStaticMarkup(node).replace(/<[^>]+>/g, '').trim()

describe('BusinessTime：排期时刻不被本地时区改写', () => {
  it('夏令时 +02:00 的 17:00 就显示 17:00', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00+02:00" />)).toBe('9/13 周日 17:00 北京')
  })

  it('冬令时 +01:00 的 10:00 就显示 10:00', () => {
    expect(text(<BusinessTime at="2026-01-05T10:00:00+01:00" />)).toBe('1/5 周一 10:00 北京')
  })

  it('同一天里 +02:00 与 +01:00 都只看墙上时刻（DST 切换日 10/25）', () => {
    expect(text(<BusinessTime at="2026-10-25T02:30:00+02:00" />)).toBe('10/25 周日 02:30 北京')
    expect(text(<BusinessTime at="2026-10-25T02:30:00+01:00" />)).toBe('10/25 周日 02:30 北京')
  })

  it('春季切换日 3/29 同理', () => {
    expect(text(<BusinessTime at="2026-03-29T03:30:00+02:00" />)).toBe('3/29 周日 03:30 北京')
  })

  it('UTC 字符串也按字面读，不换算', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00Z" />)).toBe('9/13 周日 17:00 北京')
  })

  it('时区词是格式的一部分', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00+02:00" />)).toContain('北京')
  })

  it('没有排期显示占位，不是空白', () => {
    expect(text(<BusinessTime at={null} />)).toBe('—')
    expect(text(<BusinessTime at={undefined} />)).toBe('—')
    expect(text(<BusinessTime at="" />)).toBe('—')
    expect(text(<BusinessTime at={null} fallback="暂无建议时刻" />)).toBe('暂无建议时刻')
  })

  it('解析不了的字符串也走占位，不显示 NaN', () => {
    expect(text(<BusinessTime at="不是时间" />)).toBe('—')
  })

  it('渲染成 <time>，机器可读的值是原始 ISO', () => {
    const markup = renderToStaticMarkup(<BusinessTime at="2026-09-13T17:00:00+02:00" />)
    expect(markup).toContain('<time')
    // HTML 属性名大小写不敏感。
    expect(markup.toLowerCase()).toContain('datetime="2026-09-13t17:00:00+02:00"')
    expect(markup).toContain('data-zone="business"')
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

  it('showZone={false} 只显示本地化日期和时间', () => {
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
    const at = '2026-09-13T17:00:00+02:00'
    expect(text(<BusinessTime at={at} />)).toContain('17:00')
    expect(text(<ShanghaiTime at={at} />)).toContain('23:00')
  })
})
