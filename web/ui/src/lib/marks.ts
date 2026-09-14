// 标记模型：把后端给的 highlights / risks 变成「能画出来、能逐个跳」的形状。
//
// ⛔ 下面三条是不可改变的行为，改任何一条都会让审校台在长正文上静默出错：
//      1. 下标是 Python 码点，不是 JS 的 UTF-16 码元；
//      2. 重叠时 error > risk > warn；
//      3. 按英文原文位置排序，只有德语侧位置的排最后。
//
// 两件事必须分开（web/DESIGN.md 第 8 节）：
//   🔴 highlights —— 确定性检查（regex），语义是**这里错了**
//   ⚠️ risks      —— LLM 风险预扫描，语义是**这里注意看**
// 混成一种颜色，用几次她两种都不信。所以 type 保留三档，颜色只有红黄两系。

import type { Highlight, BodyRisk, Mark, MarkSide, MarkType, TextSegment } from '@/types/domain'

export const MARK_ERROR = 'error' // 红，实心：不该动的被动了
export const MARK_WARN = 'warn' // 红，下划线：这处数字要人确认
export const MARK_RISK = 'risk' // 黄：翻起来容易翻坏的地方

// 重叠时谁盖谁。同一个 $219.99 会同时被 money(error) 和 money_review(warn)
// 命中——两个 <mark> 套不起来，得选一个。"错了"永远压过"注意看"。
const PRIORITY: Record<MarkType, number> = {
  [MARK_ERROR]: 3,
  [MARK_RISK]: 2,
  [MARK_WARN]: 1,
}

/**
 * 合并成一个有序的标记表。顺序 = 「下一处 →」的走法。
 *
 * 按英文原文的位置排序：审校是对着原文读的，跳转顺序跟着原文走才符合直觉。
 * 只有德语侧有位置的（原文没有、译文里凭空多出来的金额）排在最后——
 * 那种恰恰是最需要看见的，不能因为排不进原文顺序就被丢掉。
 */
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
      de: null, // 风险预扫描扫的是**英文原文**，德语侧没有对应位置
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

/**
 * 后端给的下标是 **Python 的字符（码点）下标**，而 JS 字符串按 UTF-16 码元索引。
 *
 * ⚠️ 这两者在这份数据上**必然对不上**：正文里全是 🚀 🐱 💛 👉 这类补充平面
 * 表情，每一个在 Python 里算 1、在 JS 里算 2。实测 1498 字符那篇的标记
 * 整体偏了 4 个字符——正好是它前面的 4 个表情。
 *
 * 所以一律先把字符串拆成**码点数组**再按下标切。契约那边不动（下标就该是
 * 码点，那是语言无关的口径），换算放在唯一需要它的这一侧。
 */
const codepoints = (text: string | null | undefined): string[] => Array.from(text ?? '')

/** 字符数，和后端（Python）数出来的是同一个数。界面上显示的长度用它。 */
export const charLength = (text: string | null | undefined): number => codepoints(text).length

/**
 * 把一段文本切成「纯文本 / 被标记」的片段序列。
 *
 * 必须按边界切而不是逐个包 <mark>：标记会重叠（同一处金额既是 error 又是
 * warn），套着写出来的 HTML 是坏的。切成不重叠的区间之后，每个区间自己
 * 挑一个优先级最高的类来画。
 *
 * 返回 [{ text, type, index }]，type 为 null 表示这段没有标记。
 * index 是该片段归属的标记序号，用来做跳转锚点。
 */
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
    // noUncheckedIndexedAccess：points 由上面的 Set 生成，i 与 i+1 一定在界内，
    // 但类型上仍是 number | undefined，所以显式取出来判一次。
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
  // 相邻的同类片段合并，少生成一堆没必要的 DOM 节点。
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
