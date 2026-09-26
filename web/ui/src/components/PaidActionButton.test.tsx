import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { PaidActionButton } from './PaidActionButton'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)

describe('模型动作保持次要且可理解', () => {
  it('只显示动作名称，不附带价格', () => {
    const markup = html(<PaidActionButton label="生成图片" onClick={vi.fn()} />)
    expect(markup).toContain('生成图片')
    expect(markup).toContain('ant-btn-default')
    expect(markup).not.toContain('US$')
    expect(markup).not.toContain('按实际用量计费')
    expect(markup).not.toContain('disabled=""')
  })
  it('禁用时保留业务可读原因，加载态可辨识', () => {
    const reason = '请先保存当前修改'
    const markup = html(<PaidActionButton label="生成图片" disabledReason={reason} />)
    expect(markup).toContain('disabled=""')
    expect(markup).toContain(`title="${reason}"`)
    expect(html(<PaidActionButton label="生成图片" loading />)).toContain('ant-btn-loading')
  })
  it('剩余次数包括零次，并保留浏览器动作标识', () => {
    expect(html(<PaidActionButton label="调整图片" remaining={0} />)).toContain('剩余 0 次')
    expect(html(<PaidActionButton label="调整图片" remaining={2} />)).toContain('剩余 2 次')
    expect(html(<PaidActionButton label="调整图片" />)).not.toContain('剩余')
    expect(html(<PaidActionButton label="调整图片" />)).toContain('data-paid-action="调整图片"')
  })
})
