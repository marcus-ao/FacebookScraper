// 标记使用 Python 码点下标；重叠时 error > risk > warn，顺序跟随英文位置。

import type { Highlight, BodyRisk, Mark, MarkSide, MarkType, TextSegment } from '@/types/domain'

export const MARK_ERROR = 'error'
export const MARK_WARN = 'warn'
export const MARK_RISK = 'risk'

const PRIORITY: Record<MarkType, number> = {
  [MARK_ERROR]: 3,
  [MARK_RISK]: 2,
  [MARK_WARN]: 1,
}

/** 按英文位置排序，仅德语有位置的标记排在最后。 */
export function buildMarks(
  highlights: readonly Highlight[] | null | undefined,
  risks: readonly BodyRisk[] | null | undefined,
): Mark[] {
  const marks: Omit<Mark, 'index'>[] = []
  for (const h of highlights ?? []) {
    marks.push({
      type: h.severity === 'error' ? MARK_ERROR : MARK_WARN,
      kind: h.kind,
      label: h.label,
      en: h.en_span ?? null,
      de: h.de_span ?? null,
    })
  }
  for (const r of risks ?? []) {
    marks.push({
      type: MARK_RISK,
      kind: r.kind,
      label: r.label,
      en: r.en_span ?? null,
      de: null, // 风险扫描只提供英文原文位置。
    })
  }
  marks.sort((a, b) => {
    const av = a.en ? a.en[0] : Number.MAX_SAFE_INTEGER
    const bv = b.en ? b.en[0] : Number.MAX_SAFE_INTEGER
    if (av !== bv) return av - bv
    return (a.de ? a.de[0] : 0) - (b.de ? b.de[0] : 0)
  })
  return marks.map((mark, index) => ({ ...mark, index }))
}

/** 先拆为码点数组，避免表情导致 Python 下标与 UTF-16 索引错位。 */
const codepoints = (text: string | null | undefined): string[] => Array.from(text ?? '')

export const charLength = (text: string | null | undefined): number => codepoints(text).length

/** 按重叠边界切成互斥片段；index 指向所属标记，用于跳转。 */
export function segment(
  text: string | null | undefined,
  marks: readonly Mark[],
  side: MarkSide,
): TextSegment[] {
  const value = text ?? ''
  const chars = codepoints(value)
  const cut = (from: number, to: number): string => chars.slice(from, to).join('')
  const spans: { start: number; end: number; mark: Mark }[] = []
  for (const mark of marks) {
    const span = side === 'en' ? mark.en : mark.de
    if (!span) continue
    const start = Math.max(0, Math.min(chars.length, span[0]))
    const end = Math.max(start, Math.min(chars.length, span[1]))
    if (end > start) spans.push({ start, end, mark })
  }
  if (!spans.length) return [{ text: value, type: null, index: -1 }]

  const cuts = new Set<number>([0, chars.length])
  for (const s of spans) {
    cuts.add(s.start)
    cuts.add(s.end)
  }
  const points = [...cuts].sort((a, b) => a - b)

  const out: TextSegment[] = []
  for (let i = 0; i < points.length - 1; i++) {
    const from = points[i]
    const to = points[i + 1]
    if (from === undefined || to === undefined) continue
    if (to <= from) continue
    const covering = spans.filter((s) => s.start <= from && s.end >= to)
    if (!covering.length) {
      out.push({ text: cut(from, to), type: null, index: -1 })
      continue
    }
    const winner = covering.reduce((best, s) =>
      PRIORITY[s.mark.type] > PRIORITY[best.mark.type] ? s : best,
    )
    out.push({
      text: cut(from, to),
      type: winner.mark.type,
      // 锚点归属**最靠前**的那个标记，这样「下一处」跳过去落在片段开头。
      index: covering.reduce((min, s) => Math.min(min, s.mark.index), Number.MAX_SAFE_INTEGER),
    })
  }
  return out.reduce<TextSegment[]>((acc, part) => {
    const last = acc[acc.length - 1]
    if (last && last.type === part.type && last.index === part.index) {
      last.text += part.text
      return acc
    }
    acc.push({ ...part })
    return acc
  }, [])
}
