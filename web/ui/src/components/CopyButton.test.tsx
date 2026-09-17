import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { CopyButton } from './CopyButton'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)

describe('没有确定的成品就不让复制', () => {
  it('text 缺失时变灰，并说明为什么', () => {
    const markup = html(<CopyButton text={undefined} label="复制发布文案" />)
    expect(markup).toContain('disabled=""')
    expect(markup).toContain('还没有可复制的成品文案')
  })

  it('空字符串同样算没有成品', () => {
    expect(html(<CopyButton text="" label="复制发布文案" />)).toContain('disabled=""')
  })

  it('校验未回来时由调用方给出更具体的原因', () => {
    const markup = html(
      <CopyButton text="Sauber jetzt" label="复制发布文案" disabledReason="正在校验，请稍候取准确文案" />,
    )
    expect(markup).toContain('disabled=""')
    expect(markup).toContain('正在校验，请稍候取准确文案')
  })

  it('有成品就可以点', () => {
    const markup = html(<CopyButton text="Sauber jetzt #Neakasa" label="复制发布文案" />)
    expect(markup).not.toContain('disabled=""')
    expect(markup).toContain('复制发布文案')
  })
})
