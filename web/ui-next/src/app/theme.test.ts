import { describe, expect, it } from 'vitest'

import { contrastRatio, hueDistance, hueOf } from '@/lib/contrast'
import { antdComponents, antdToken, cssVariables, tokens } from './theme'

const { neutral, primary, status, space, typography, layout } = tokens

// ─────────────────────────────────────────────────────────────────────────────
// 主色定值（Stage B1 的一项交付）
//
// DESIGN.md §5.2 给了三条判据，§15 把"主色的具体值"列为 Stage B1 要定的事。
// 这一组断言就是那个决定的**可复算依据** —— 换任何一个值，这里会当场红。
// ─────────────────────────────────────────────────────────────────────────────

describe('最终主色 #155EEF', () => {
  it('就是 #155EEF，不是 Stage A 的占位值', () => {
    expect(primary.base).toBe('#155EEF')
  })

  it('判据 1：填充按钮上的白字 ≥ 4.5:1', () => {
    // 主按钮上写的是「通过并创建排期」这种不能看错的文案，
    // 而她一天要按几十次。4.5:1 是 WCAG AA 的正文门槛。
    expect(contrastRatio(primary.base, primary.fg)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(primary.base, '#ffffff')).toBeCloseTo(5.41, 1)
  })

  it('判据 1 续：hover / active 只会让对比度更高，不会更低', () => {
    // antd 默认的 hover 是**变浅**，白字对比度会在 hover 那一刻掉下 4.5。
    // 我们的 hover/active 往深走，所以这条永远成立。
    const base = contrastRatio(primary.base, '#ffffff')
    expect(contrastRatio(primary.strong, '#ffffff')).toBeGreaterThan(base)
    expect(contrastRatio(primary.deep, '#ffffff')).toBeGreaterThan(base)
    expect(antdToken.colorPrimaryHover).toBe(primary.strong)
    expect(antdToken.colorPrimaryActive).toBe(primary.deep)
  })

  it('判据 2：与错误红明显区分', () => {
    expect(hueDistance(primary.base, status.error)).toBeGreaterThan(90)
  })

  it('判据 3：与风险黄明显区分', () => {
    expect(hueDistance(primary.base, status.risk)).toBeGreaterThan(90)
  })

  it('判据 4：焦点环在白底与 #f1f5f9 灰底上都可见（非文本 ≥ 3:1）', () => {
    expect(contrastRatio(primary.base, neutral.surface)).toBeGreaterThanOrEqual(3)
    expect(contrastRatio(primary.base, neutral.bg)).toBeGreaterThanOrEqual(3)
  })

  it('判据 5：不是"通用 AI 紫"', () => {
    // 旧 UI 的 #4f46e5 色相 ~244°，正好在提纲点名要避开的区间里。
    const hue = hueOf(primary.base)
    expect(hue).toBeGreaterThan(200)
    expect(hue).toBeLessThan(240)
    expect(hueOf('#4f46e5')).toBeGreaterThan(240) // 对照：旧主色确实在紫区
  })

  it('浅底上的前景用 strong 而不是 base —— 12px 不适用大文本豁免', () => {
    expect(primary.onSoft).toBe(primary.strong)
    expect(contrastRatio(primary.onSoft, primary.soft)).toBeGreaterThanOrEqual(4.5)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 其余颜色的可读性
// ─────────────────────────────────────────────────────────────────────────────

describe('状态色与中性色的对比度', () => {
  // 断言按"这个颜色实际落在什么底色上"分组，不搞一刀切。
  // 内容都在白色表面上（表格行、卡片）；#f1f5f9 是页面底，内容区不在上面写字。

  it.each([
    ['错误红', status.error],
    ['风险黄', status.risk],
    ['成功绿', status.success],
    ['次要文字', neutral.fgMuted],
    ['正文', neutral.fg],
  ])('%s 作为文字，在白色表面上 ≥ 4.5:1', (_name, color) => {
    expect(contrastRatio(color, neutral.surface)).toBeGreaterThanOrEqual(4.5)
  })

  it.each([
    ['次要文字', neutral.fgMuted],
    ['正文', neutral.fg],
  ])('%s 在页面底色 #f1f5f9 上也 ≥ 4.5:1', (_name, color) => {
    // 这两个确实会直接写在页面底上（面包屑、空状态说明）。
    // 12px 的元信息不适用大文本豁免（DESIGN.md §13）。
    expect(contrastRatio(color, neutral.bg)).toBeGreaterThanOrEqual(4.5)
  })

  it.each([
    ['错误红', status.error, neutral.bg],
    ['风险黄', status.risk, neutral.bg],
    ['错误红在自己的浅底上', status.error, status.errorSoft],
    ['风险黄在自己的浅底上', status.risk, status.riskSoft],
  ])('%s 作为图标/标记线 ≥ 3:1（WCAG 非文本门槛）', (_name, color, on) => {
    // ⚠️ 记一笔实测值，免得以后有人以为它们也过了 4.5：
    //    #dc2626 on #f1f5f9 = 4.41:1
    //    #dc2626 on #fee2e2 = 3.95:1
    //    #b45309 on #fef3c7 = 4.28:1
    // 所以这两个颜色**只做图标、下划线、边框与正文标记的底**，
    // 不做 12px 的彩色文字。ProblemIndicator 是图标 + Tooltip，符合这条；
    // 正文标记 mark.mk-* 的文字色是继承的 #0f172a，也符合。
    // 值本身沿用旧 UI 不动（DESIGN.md §5.4：已在真实中英德正文上看过）。
    expect(contrastRatio(color, on)).toBeGreaterThanOrEqual(3)
  })

  it('成功浅底上的成功前景 ≥ 4.5:1（已排期 / 已通过的 Tag 是彩色文字）', () => {
    expect(contrastRatio(status.success, status.successSoft)).toBeGreaterThanOrEqual(4.5)
  })

  it('中性 Tag：muted 前景在中性浅底上 ≥ 4.5:1', () => {
    expect(contrastRatio(neutral.fgMuted, neutral.bg)).toBeGreaterThanOrEqual(4.5)
  })

  it('红与黄本身也要能互相区分', () => {
    // 混成一种，用几次她两种都不信（DESIGN.md §5.1 第 1 条）。
    expect(hueDistance(status.error, status.risk)).toBeGreaterThan(15)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// token 单一来源
//
// 这一组是 DESIGN.md §2 的机器化断言：antd 那边和 CSS 变量那边**必须同源**。
// 另有一条更硬的纪律（src/ 里不许出现字面值）在 design-discipline.test.ts。
// ─────────────────────────────────────────────────────────────────────────────

/** 把 tokens 里所有字符串叶子摊平，用来证明"CSS 变量的值都是派生来的"。 */
function flattenLiterals(node: unknown, out: Set<string> = new Set()): Set<string> {
  if (typeof node === 'string' || typeof node === 'number') {
    out.add(String(node))
    return out
  }
  if (node && typeof node === 'object') {
    for (const value of Object.values(node)) flattenLiterals(value, out)
  }
  return out
}

describe('token 单一来源', () => {
  const literals = flattenLiterals(tokens)

  it('每一个 --rc-* 变量的值都能在 tokens 里找到来源', () => {
    const derived = new Set<string>()
    for (const value of Object.values(cssVariables)) derived.add(value)

    const orphans = [...derived].filter((value) => {
      const stripped = value.replace(/px$/, '')
      return !literals.has(value) && !literals.has(stripped)
    })
    expect(orphans).toEqual([])
  })

  it('antd 的 token 值同样来自 tokens，没有第二份颜色表', () => {
    const orphans = Object.entries(antdToken)
      .filter(([, value]) => typeof value === 'string' || typeof value === 'number')
      .filter(([, value]) => !literals.has(String(value)))
      // 这几项是 antd 自己的开关/字体栈，不是设计 token。
      .filter(([key]) => !['wireframe', 'sizeUnit', 'sizeStep', 'fontFamily'].includes(key))
      .map(([key, value]) => `${key}=${String(value)}`)
    expect(orphans).toEqual([])
  })

  it('组件级覆盖也只用 tokens 里的值（含 `0 16px` 这种组合值里的每一个数）', () => {
    const orphans: string[] = []
    for (const [component, overrides] of Object.entries(antdComponents)) {
      for (const [key, value] of Object.entries(overrides)) {
        if (typeof value !== 'string' && typeof value !== 'number') continue
        const text = String(value)
        if (text === 'transparent') continue // 表头分隔线：要的就是"没有"
        if (literals.has(text)) continue
        // 组合值（`0 16px`）：把里面的数逐个拆出来验，0 例外。
        const numbers = text.match(/[\d.]+/g)
        const looksComposed = numbers !== null && numbers.length > 0
        if (looksComposed && numbers.every((n) => n === '0' || literals.has(n))) continue
        orphans.push(`${component}.${key}=${text}`)
      }
    }
    expect(orphans).toEqual([])
  })

  it('外壳尺寸由 token 驱动：48 / 200 / 48', () => {
    // AppShell 不许把这三个数写死 —— 它们同时要进 antd 的 Layout 和 CSS 变量。
    expect(layout.topbarHeight).toBe(48)
    expect(layout.sidebarWidth).toBe(200)
    expect(layout.sidebarCollapsedWidth).toBe(48)
    expect(antdComponents.Layout.headerHeight).toBe(layout.topbarHeight)
    expect(antdComponents.Menu.collapsedWidth).toBe(layout.sidebarCollapsedWidth)
    expect(cssVariables['--rc-topbar-h']).toBe('48px')
    expect(cssVariables['--rc-sidebar-w']).toBe('200px')
    expect(cssVariables['--rc-sidebar-collapsed-w']).toBe('48px')
  })

  it('间距只有六档，全部在 4 的刻度上', () => {
    const values = Object.values(space)
    expect(values).toEqual([4, 8, 12, 16, 24, 32])
    for (const value of values) expect(value % 4).toBe(0)
  })

  it('排版基线：fontSize 14 / controlHeight 32 / borderRadius 6', () => {
    expect(typography.body).toBe(14)
    expect(layout.controlHeight).toBe(32)
    expect(layout.borderRadius).toBe(6)
    expect(antdToken.fontSize).toBe(14)
    expect(antdToken.controlHeight).toBe(32)
    expect(antdToken.borderRadius).toBe(6)
  })

  it('正文审校的例外：15px / 1.7', () => {
    expect(typography.proseSize).toBe(15)
    expect(typography.proseLineHeight).toBe(1.7)
  })

  it('Layout.Header 的底色被改成白 —— antd 默认是深色 #001529', () => {
    expect(antdComponents.Layout.headerBg).toBe(neutral.surface)
    expect(antdComponents.Layout.siderBg).toBe(neutral.surface)
  })

  it('阴影只有浮层一档', () => {
    expect(Object.keys(tokens.elevation)).toEqual(['overlay'])
    expect(antdToken.boxShadow).toBe(tokens.elevation.overlay)
  })
})
