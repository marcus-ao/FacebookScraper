import { describe, expect, it } from 'vitest'

import { adopt } from './SuggestionPanel'
import type { TextSuggestion } from '@/types/domain'

const item = (quote: string, replacement: string): TextSuggestion =>
  ({ quote, replacement, kind: 'grammar', why: '词尾错了' })

describe('采用一条建议', () => {
  it('片段唯一时按原样替换', () => {
    expect(adopt('Kostenlose Versand ab $50.', item('Kostenlose Versand', 'Kostenloser Versand')))
      .toBe('Kostenloser Versand ab $50.')
  })

  it('正文已经改过、片段找不到时返回 null，而不是猜一个位置', () => {
    expect(adopt('Gratis Versand ab $50.', item('Kostenlose Versand', 'Kostenloser Versand')))
      .toBeNull()
  })

  it('⛔ 片段出现多次时拒绝替换 —— 改错地方比不改更糟', () => {
    expect(adopt('Versand und Versand', item('Versand', 'Lieferung'))).toBeNull()
  })

  it('只替换一处，不做全局替换', () => {
    const body = 'Neu: der Versand. Mehr zum Thema Versandkosten.'
    expect(adopt(body, item('der Versand', 'die Lieferung')))
      .toBe('Neu: die Lieferung. Mehr zum Thema Versandkosten.')
  })

  it('替换内容里的正则元字符按字面处理', () => {
    expect(adopt('Preis (ab) heute', item('(ab)', '$& ab')))
      .toBe('Preis $& ab heute')
  })
})
