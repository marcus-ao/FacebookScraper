import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { BusinessTime, ShanghaiTime } from './Time'

/** 在第三时区运行，检测对宿主时区的隐式依赖。 */

const text = (node: React.ReactElement) =>
  renderToStaticMarkup(node).replace(/<[^>]+>/g, '').trim()

describe('BusinessTime：排期时刻统一按业务时钟显示', () => {
  it('夏令时 +02:00 的 17:00 是业务时钟 23:00', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00+02:00" />)).toBe('9/13 周日 23:00')
  })

  it('冬令时 +01:00 的 10:00 是业务时钟 17:00', () => {
    expect(text(<BusinessTime at="2026-01-05T10:00:00+01:00" />)).toBe('1/5 周一 17:00')
  })

  it('夏令时重复钟点由各自偏移转换为不同瞬间', () => {
    expect(text(<BusinessTime at="2026-10-25T02:30:00+02:00" />)).toBe('10/25 周日 08:30')
    expect(text(<BusinessTime at="2026-10-25T02:30:00+01:00" />)).toBe('10/25 周日 09:30')
  })

  it('春季切换日也按绝对时刻转换', () => {
    expect(text(<BusinessTime at="2026-03-29T03:30:00+02:00" />)).toBe('3/29 周日 09:30')
  })

  it('UTC 字符串换算，无偏移的输入保留原值', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00Z" />)).toBe('9/14 周一 01:00')
    expect(text(<BusinessTime at="2026-09-25T04:30" />)).toBe('9/25 周五 04:30')
  })

  it('全站统一业务墙上时刻，不再附时区词', () => {
    expect(text(<BusinessTime at="2026-09-13T17:00:00+02:00" />)).not.toContain('北京')
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
    expect(text(<ShanghaiTime at="2026-09-13T07:51:04Z" />)).toBe('2026/09/13 15:51')
  })

  it('跨日：UTC 20:00 在上海已经是第二天 04:00', () => {
    expect(text(<ShanghaiTime at="2026-09-13T20:00:00Z" />)).toBe('2026/09/14 04:00')
  })

  it('无偏移的业务时钟输入保持原样，不按浏览器本地时区解释', () => {
    expect(text(<ShanghaiTime at="2026-09-25T04:30" />)).toBe('2026/09/25 04:30')
  })

  it('上海没有夏令时，冬夏两个日期都是 UTC+8', () => {
    expect(text(<ShanghaiTime at="2026-01-15T00:00:00Z" />)).toBe('2026/01/15 08:00')
    expect(text(<ShanghaiTime at="2026-07-15T00:00:00Z" />)).toBe('2026/07/15 08:00')
  })

  it('不再附时区词', () => {
    expect(text(<ShanghaiTime at="2026-09-13T07:51:04Z" />)).not.toContain('上海')
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

describe('两个时间组件使用同一业务钟点', () => {
  it('同一个绝对瞬间显示相同小时，格式可不同', () => {
    const at = '2026-09-13T17:00:00+02:00'
    expect(text(<BusinessTime at={at} />)).toContain('23:00')
    expect(text(<ShanghaiTime at={at} />)).toContain('23:00')
  })
})
