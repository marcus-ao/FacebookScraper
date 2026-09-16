import { describe, expect, it } from 'vitest'

import { adopt, suggestionsCurrent } from './SuggestionPanel'
import type { TextSuggestion, TextSuggestions } from '@/types/domain'
import type { Sha256 } from '@/types/brands'

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

describe('建议与当前编辑区绑定', () => {
  const body = 'Kostenlose Versand. Noch ein Satz.'
  const stored: TextSuggestions = {
    job_id: 'job1', body_de: body, source_text_sha256: 'source' as Sha256, text_de_sha256: 'body' as Sha256,
    items: [], dropped: [], current: false, generated_at: '', prompt_version: 2, current_prompt_version: 2,
  }

  it('建议针对尚未保存的正文时仍可采用', () => {
    expect(suggestionsCurrent(stored, 'source', body)).toBe(true)
  })

  it('即使建议片段没变，其他句子的编辑也立即使旧建议失效', () => {
    expect(suggestionsCurrent(stored, 'source', body + ' Neu.')).toBe(false)
  })

  it('源文或模板版本变动，以及旧记录缺少正文快照时都失效', () => {
    expect(suggestionsCurrent(stored, 'changed', body)).toBe(false)
    expect(suggestionsCurrent({ ...stored, current_prompt_version: 3 }, 'source', body)).toBe(false)
    expect(suggestionsCurrent({ ...stored, body_de: null }, 'source', body)).toBe(false)
  })
})
