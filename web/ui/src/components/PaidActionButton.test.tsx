import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { PaidActionButton } from './PaidActionButton'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)

describe('付费动作不是 primary', () => {
  it('是 default 形态', () => {
    const markup = html(<PaidActionButton label="初翻" amount="US$0.020" />)
    expect(markup).toContain('ant-btn-default')
    expect(markup).not.toContain('ant-btn-primary')
  })

  it('也不是 danger', () => {
    const markup = html(<PaidActionButton label="图片优化" amount="US$0.045" />)
    expect(markup).not.toContain('ant-btn-dangerous')
  })
})

describe('金额写在按钮上', () => {
  it('动作 + 金额同时可见', () => {
    const markup = html(<PaidActionButton label="图片优化" amount="US$0.045" />)
    expect(markup).toContain('图片优化')
    expect(markup).toContain('US$0.045')
  })

  it('剩余次数在旁边，没有就不画', () => {
    expect(html(<PaidActionButton label="初翻" amount="US$0.020" remaining={12} />)).toContain(
      '剩余 12 次',
    )
    expect(html(<PaidActionButton label="初翻" amount="US$0.020" />)).not.toContain('剩余')
  })

  it('剩余 0 次仍然显示 —— 0 是信息，不是"没有值"', () => {
    expect(html(<PaidActionButton label="初翻" amount="US$0.020" remaining={0} />)).toContain(
      '剩余 0 次',
    )
  })
})

describe('disabled 必须给原因', () => {
  const reason = '这一篇的第三方作者还没授权，先在上面完成授权再初翻。'

  it('不传原因就是可点的', () => {
    const markup = html(<PaidActionButton label="初翻" amount="US$0.020" onClick={vi.fn()} />)
    expect(markup).not.toContain('disabled=""')
  })

  it('传了原因才变灰', () => {
    const markup = html(
      <PaidActionButton label="初翻" amount="US$0.020" disabledReason={reason} />,
    )
    expect(markup).toContain('disabled=""')
  })

  it('原因在 DOM 里拿得到，不只活在 Tooltip 的 portal 里', () => {
    // 禁用原因也通过 title 提供，不只依赖浮层。
    const markup = html(
      <PaidActionButton label="初翻" amount="US$0.020" disabledReason={reason} />,
    )
    expect(markup).toContain(`title="${reason}"`)
  })

  it('⛔ 构造不出"灰着但没有理由"的实例', () => {
    // @ts-expect-error disabled 不是这个组件的 prop
    const bad = <PaidActionButton label="初翻" amount="US$0.020" disabled />
    expect(bad).toBeDefined()
  })

  it('禁用状态始终显示可读原因', () => {
    const markup = html(
      <PaidActionButton
        label="文案优化"
        amount="US$0.012"
        disabledReason="正在编辑德语译文，先保存或放弃当前修改。"
      />,
    )
    expect(markup).toContain('正在编辑德语译文，先保存或放弃当前修改。')
  })
})

describe('其它', () => {
  it('loading 时转圈', () => {
    expect(html(<PaidActionButton label="初翻" amount="US$0.020" loading />)).toContain(
      'ant-btn-loading',
    )
  })

  it('带 data-paid-action，给浏览器级断言用', () => {
    expect(html(<PaidActionButton label="初翻" amount="US$0.020" />)).toContain(
      'data-paid-action="初翻"',
    )
  })

  it('金额用等宽数位', () => {
    const markup = html(<PaidActionButton label="初翻" amount="US$0.020" remaining={3} />)
    expect(markup).toContain('_button_')
    expect(markup).toContain('_remaining_')
  })
})
