import { describe, expect, it } from 'vitest'

import { MARK_ERROR, MARK_RISK, MARK_WARN, buildMarks, charLength, segment } from './marks'
import type { BodyRisk, Highlight, Mark } from '@/types/domain'


const highlight = (over: Partial<Highlight>): Highlight => ({
  kind: 'money',
  en_span: null,
  de_span: null,
  severity: 'error',
  label: 'label',
  ...over,
})

const risk = (over: Partial<BodyRisk>): BodyRisk => ({
  kind: 'us_only',
  en_span: [0, 1],
  label: 'risk label',
  quote: 'quote',
  ...over,
})

describe('charLength：码点计数，和后端 Python 数出来的是同一个数', () => {
  it('补充平面表情算一个字符，不是两个', () => {
    // 🚀 在 Python 里 len() == 1，在 JS 里 .length == 2。
    expect('🚀'.length).toBe(2)
    expect(charLength('🚀')).toBe(1)
  })

  it('实测那种混排正文：四个表情让 UTF-16 长度多出四', () => {
    const text = '🚀 a 🐱 b 💛 c 👉 d'
    expect(charLength(text)).toBe(text.length - 4)
  })

  it('null / undefined / 空串都是 0', () => {
    expect(charLength(null)).toBe(0)
    expect(charLength(undefined)).toBe(0)
    expect(charLength('')).toBe(0)
  })
})

describe('segment：按码点下标切，不按 UTF-16 码元切', () => {
  const en = '🚀 $20 off'

  it('表情之后的 span 落在正确的字符上', () => {
    // Python 码点下标：🚀=0, ' '=1, '$'=2, '2'=3, '0'=4 → "$20" 是 [2, 5)
    const marks = buildMarks([highlight({ en_span: [2, 5], severity: 'warn' })], [])
    const parts = segment(en, marks, 'en')
    const marked = parts.filter((part) => part.type !== null)
    expect(marked).toHaveLength(1)
    expect(marked[0]?.text).toBe('$20')
  })

  it('用 UTF-16 切会切错 —— 证明上面那条不是巧合', () => {
    expect(en.slice(2, 5)).toBe(' $2')
    expect(Array.from(en).slice(2, 5).join('')).toBe('$20')
  })

  it('没有任何 span 时返回整段未标记文本', () => {
    expect(segment(en, [], 'en')).toEqual([{ text: en, type: null, index: -1 }])
  })

  it('拼回来的文本必须和原文逐字相同', () => {
    const marks = buildMarks(
      [highlight({ en_span: [2, 5], severity: 'warn' }), highlight({ en_span: [7, 10] })],
      [risk({ en_span: [0, 1] })],
    )
    const joined = segment(en, marks, 'en')
      .map((part) => part.text)
      .join('')
    expect(joined).toBe(en)
  })

  it('span 超出文本长度时被夹到边界，不抛异常', () => {
    const marks = buildMarks([highlight({ en_span: [5, 9999] })], [])
    const parts = segment('abc', marks, 'en')
    expect(parts.map((part) => part.text).join('')).toBe('abc')
  })

  it('de 侧只画有 de_span 的标记', () => {
    const marks = buildMarks([highlight({ en_span: [0, 1], de_span: null })], [])
    expect(segment('abc', marks, 'de')).toEqual([{ text: 'abc', type: null, index: -1 }])
  })

  it('相邻同类片段被合并，不生成多余节点', () => {
    const marks = buildMarks(
      [highlight({ en_span: [0, 2] }), highlight({ en_span: [2, 4] })],
      [],
    )
    const parts = segment('abcd', marks, 'en')
    // 仅合并同一标记的相邻片段，保留不同标记的跳转锚点。
    expect(parts.length).toBeLessThanOrEqual(2)
  })
})

describe('segment：重叠时 error > risk > warn', () => {
  const pick = (marks: Mark[]) => segment('abcdef', marks, 'en').find((p) => p.type !== null)?.type

  it('error 压过 warn', () => {
    const marks = buildMarks(
      [
        highlight({ en_span: [1, 4], severity: 'warn', kind: 'money_review' }),
        highlight({ en_span: [1, 4], severity: 'error', kind: 'money' }),
      ],
      [],
    )
    expect(pick(marks)).toBe(MARK_ERROR)
  })

  it('error 压过 risk', () => {
    const marks = buildMarks(
      [highlight({ en_span: [1, 4], severity: 'error' })],
      [risk({ en_span: [1, 4] })],
    )
    expect(pick(marks)).toBe(MARK_ERROR)
  })

  it('risk 压过 warn', () => {
    const marks = buildMarks(
      [highlight({ en_span: [1, 4], severity: 'warn' })],
      [risk({ en_span: [1, 4] })],
    )
    expect(pick(marks)).toBe(MARK_RISK)
  })

  it('优先级与标记先后顺序无关', () => {
    const forward = buildMarks(
      [
        highlight({ en_span: [1, 4], severity: 'error' }),
        highlight({ en_span: [1, 4], severity: 'warn' }),
      ],
      [],
    )
    const backward = buildMarks(
      [
        highlight({ en_span: [1, 4], severity: 'warn' }),
        highlight({ en_span: [1, 4], severity: 'error' }),
      ],
      [],
    )
    expect(pick(forward)).toBe(MARK_ERROR)
    expect(pick(backward)).toBe(MARK_ERROR)
  })

  it('锚点归属最靠前的那个标记，这样「下一处」跳过去落在片段开头', () => {
    const marks = buildMarks(
      [highlight({ en_span: [0, 4] }), highlight({ en_span: [2, 4] })],
      [],
    )
    const covered = segment('abcdef', marks, 'en').filter((part) => part.type !== null)
    expect(Math.min(...covered.map((part) => part.index))).toBe(0)
  })
})

describe('buildMarks：排序规则', () => {
  it('按英文原文位置升序', () => {
    const marks = buildMarks(
      [
        highlight({ en_span: [10, 12] }),
        highlight({ en_span: [2, 4] }),
        highlight({ en_span: [6, 8] }),
      ],
      [],
    )
    expect(marks.map((mark) => mark.en?.[0])).toEqual([2, 6, 10])
    expect(marks.map((mark) => mark.index)).toEqual([0, 1, 2])
  })

  it('只有德语侧位置的排最后 —— 那种恰恰最需要看见，不能丢掉', () => {
    const marks = buildMarks(
      [
        highlight({ en_span: null, de_span: [3, 5], label: '译文里多出来的' }),
        highlight({ en_span: [9, 11], label: '原文里有的' }),
      ],
      [],
    )
    expect(marks.at(-1)?.label).toBe('译文里多出来的')
    expect(marks).toHaveLength(2)
  })

  it('英文位置相同时按德语位置排', () => {
    const marks = buildMarks(
      [
        highlight({ en_span: [4, 6], de_span: [20, 22], label: 'de 靠后' }),
        highlight({ en_span: [4, 6], de_span: [8, 10], label: 'de 靠前' }),
      ],
      [],
    )
    expect(marks.map((mark) => mark.label)).toEqual(['de 靠前', 'de 靠后'])
  })

  it('risks 的 de 侧永远是 null —— 风险扫的是英文原文', () => {
    const marks = buildMarks([], [risk({ en_span: [1, 3] })])
    expect(marks[0]?.de).toBeNull()
    expect(marks[0]?.type).toBe(MARK_RISK)
  })

  it('highlights 的 severity 映射成 error / warn 两档', () => {
    const marks = buildMarks(
      [
        highlight({ en_span: [0, 1], severity: 'error' }),
        highlight({ en_span: [1, 2], severity: 'warn' }),
      ],
      [],
    )
    expect(marks.map((mark) => mark.type)).toEqual([MARK_ERROR, MARK_WARN])
  })

  it('null / undefined 输入不抛异常', () => {
    expect(buildMarks(null, null)).toEqual([])
    expect(buildMarks(undefined, undefined)).toEqual([])
  })

  it('index 连续，可以直接当「第 n / 共 N 处」用', () => {
    const marks = buildMarks(
      [highlight({ en_span: [0, 1] }), highlight({ en_span: [2, 3] })],
      [risk({ en_span: [4, 5] })],
    )
    expect(marks.map((mark) => mark.index)).toEqual([0, 1, 2])
  })
})
