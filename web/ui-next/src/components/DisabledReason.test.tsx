import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Button } from 'antd'

import { DisabledReason } from './DisabledReason'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)
const reason = '请先填写柏林发布时间'

describe('灰按钮的原因，只用键盘也要拿得到', () => {
  it('有原因时外层是一个可聚焦的注记', () => {
    // 原生 disabled 的 button 拿不到焦点，Tab 会直接跳过它。
    // 键盘用户能停下来的必须是外面这一层。
    const markup = html(
      <DisabledReason label="通过并创建排期" reason={reason}>
        <Button type="primary" disabled>通过并创建排期</Button>
      </DisabledReason>,
    )
    expect(markup).toContain('tabindex="0"')
    expect(markup).toContain('role="note"')
  })

  it('读到的是「动作名。当前不能操作：原因」，不是一个没名字的 span', () => {
    const markup = html(
      <DisabledReason label="通过并创建排期" reason={reason}>
        <Button type="primary" disabled>通过并创建排期</Button>
      </DisabledReason>,
    )
    expect(markup).toContain(`aria-label="通过并创建排期。当前不能操作：${reason}"`)
  })

  it('原生 title 兜底 —— Tooltip 的内容走 portal，只在悬停时才存在', () => {
    const markup = html(
      <DisabledReason label="生成图片 · 约 US$0.045" reason="这张图片的优化次数已用完">
        <Button disabled>生成图片</Button>
      </DisabledReason>,
    )
    expect(markup).toContain('title="这张图片的优化次数已用完"')
  })

  it('⛔ 按钮本身还是 disabled，没有被改成"能点但拦 click"', () => {
    // 那样会让读屏把按钮念成可用的，按下去却什么都不发生 —— 比灰着更糟。
    const markup = html(
      <DisabledReason label="通过并创建排期" reason={reason}>
        <Button type="primary" disabled>通过并创建排期</Button>
      </DisabledReason>,
    )
    expect(markup).toContain('disabled=""')
  })

  it('没有原因时这一层整个不存在，不留多余的 tab stop', () => {
    // 页面上每一个能停的地方都要值得停。
    const markup = html(
      <DisabledReason label="通过并创建排期" reason="">
        <Button type="primary">通过并创建排期</Button>
      </DisabledReason>,
    )
    expect(markup).not.toContain('tabindex="0"')
    expect(markup).not.toContain('role="note"')
    expect(markup).not.toContain('disabled=""')
    expect(markup).toContain('通过并创建排期')
  })
})
