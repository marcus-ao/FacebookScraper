import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { antdToken, cssVariables } from './theme'

/**
 * 设计与实现纪律的机器化断言。
 *
 * 这一份守的不是某个组件，是**规矩本身**。
 *
 * 旧 UI 的问题不是没有设计系统 —— `--space-1..6` 就定在 `styles.css` 里，
 * 值和我们现在用的一模一样。问题是**没人遵守**：ApprovalPanel 写 18px、
 * CalendarPanel 写 20px、SettingsPanel 写 24px，五个区块五个内边距。
 *
 * 一份没人执行的规矩等于没有规矩。所以这里让它变成会红的测试。
 */

const SRC = fileURLToPath(new URL('..', import.meta.url))

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  }
  return out
}

const ALL_FILES = walk(SRC)
const rel = (path: string) => relative(SRC, path).replace(/\\/g, '/')

/** 这个文件自己。它满篇都是要禁的字符串，扫描时必须排除掉。 */
const SELF = 'app/design-discipline.test.ts'

/**
 * 去掉注释再扫。
 *
 * 这一步不是放水，是**扫描对象的定义**：规矩管的是代码，不是解释规矩的那句话。
 * 比如 global.css 里写着「⛔ `scroll-behavior: smooth` 全局禁用」——
 * 那行注释正是我们想要的东西，它不该让检查变红。
 *
 * `[^:\w]` 那个前置条件是为了不把 `https://` 里的 `//` 当成行注释。
 */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:\w])\/\/[^\n]*/g, '$1')
}

const read = (path: string) => stripComments(readFileSync(path, 'utf8'))
/** 需要看原文（含注释）时用它。 */
const readRaw = (path: string) => readFileSync(path, 'utf8')

const SCANNED = ALL_FILES.filter((path) => rel(path) !== SELF)
const CSS_FILES = SCANNED.filter((path) => path.endsWith('.css'))
const MODULE_CSS = CSS_FILES.filter((path) => path.endsWith('.module.css'))
const TS_FILES = SCANNED.filter((path) => /\.tsx?$/.test(path))
const SOURCE_TS = TS_FILES.filter((path) => !path.includes('.test.'))

// ─────────────────────────────────────────────────────────────────────────────
// 一、颜色只有一个真相源
// ─────────────────────────────────────────────────────────────────────────────

describe('颜色字面值只在 app/theme.ts 里', () => {
  it('样式表里一个十六进制颜色都没有', () => {
    const offenders: string[] = []
    for (const path of CSS_FILES) {
      const hits = read(path).match(/#[0-9a-fA-F]{3,8}\b/g)
      if (hits) offenders.push(`${rel(path)}: ${hits.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('样式表里也没有 rgb() / hsl() 这类写法', () => {
    const offenders: string[] = []
    for (const path of CSS_FILES) {
      const hits = read(path).match(/\b(rgba?|hsla?|color-mix)\(/g)
      if (hits) offenders.push(`${rel(path)}: ${hits.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('TS/TSX 里除了 theme.ts 与它的测试，没有颜色字面值', () => {
    const offenders: string[] = []
    for (const path of TS_FILES) {
      const name = rel(path)
      // theme.ts 是真相源；两份测试要拿字面值当对照，contrast 的测试也要。
      if (['app/theme.ts', 'app/theme.test.ts', 'lib/contrast.test.ts'].includes(name)) continue
      const hits = read(path).match(/(['"`])#[0-9a-fA-F]{3,8}\1/g)
      if (hits) offenders.push(`${name}: ${hits.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 二、刻度纪律
// ─────────────────────────────────────────────────────────────────────────────

describe('字号、间距、圆角只走语义变量', () => {
  /** 抓 `属性: ... 17px ...` 这种写法。 */
  function findPxLiterals(css: string, properties: readonly string[]): string[] {
    const out: string[] = []
    for (const property of properties) {
      const pattern = new RegExp(`(^|[\\s;{])${property}\\s*:([^;}]*)`, 'g')
      for (const match of css.matchAll(pattern)) {
        const value = match[2] ?? ''
        const px = value.match(/\b\d+(\.\d+)?px\b/g)
        if (px) out.push(`${property}: ${value.trim()}`)
      }
    }
    return out
  }

  it('组件样式表里没有 font-size / padding / margin / gap / border-radius 的 px 字面值', () => {
    // 「这个组件 padding: 17px，另一个 padding: 18px」正是要消灭的东西。
    const offenders: string[] = []
    for (const path of MODULE_CSS) {
      const hits = findPxLiterals(read(path), [
        'font-size',
        'padding',
        'padding-block',
        'padding-inline',
        'padding-top',
        'padding-bottom',
        'padding-left',
        'padding-right',
        'margin',
        'margin-block',
        'margin-inline',
        'margin-top',
        'margin-bottom',
        'gap',
        'row-gap',
        'column-gap',
        'border-radius',
      ])
      if (hits.length > 0) offenders.push(`${rel(path)}: ${hits.join(' | ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('组件样式表里也没有内联 px 高度', () => {
    const offenders: string[] = []
    for (const path of MODULE_CSS) {
      const hits = findPxLiterals(read(path), ['height', 'min-height', 'max-height', 'line-height'])
      if (hits.length > 0) offenders.push(`${rel(path)}: ${hits.join(' | ')}`)
    }
    expect(offenders).toEqual([])
  })

  it('每个组件样式表都确实在用 --rc-* 变量', () => {
    // 反向确认：不是靠"整份文件没写样式"通过上面两条。
    for (const path of MODULE_CSS) {
      expect(read(path), rel(path)).toContain('var(--rc-')
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 三、无障碍底线
// ─────────────────────────────────────────────────────────────────────────────

describe('无障碍底线', () => {
  const globalCss = readRaw(join(SRC, 'styles/global.css'))

  it('有全局 focus-visible，2px 主色 + 2px offset', () => {
    expect(globalCss).toContain(':focus-visible')
    expect(globalCss).toMatch(
      /outline:\s*var\(--rc-focus-w\)\s+solid\s+var\(--rc-primary\)\s*!important/,
    )
    expect(globalCss).toMatch(/outline-offset:\s*var\(--rc-focus-offset\)\s*!important/)
    // 值本身的真相源在 theme.ts。
    expect(cssVariables['--rc-focus-w']).toBe('2px')
    expect(cssVariables['--rc-focus-offset']).toBe('2px')
  })

  it('焦点环压得过 antd 那八条组件级规则', () => {
    // antd 的 `:focus-visible` 规则特指度最高到 0,4,0，且都写死 offset 1px。
    // 用选择器去追等于把它的内部类名抄进来，每次升级都要重对一遍。
    expect(globalCss).toContain('html:root :focus-visible')
    expect(globalCss).toContain('!important')
  })

  it('antd 自己画的焦点宽度也是 2px', () => {
    expect(antdToken.lineWidthFocus).toBe(2)
  })

  it('⛔ 任何地方都没有 outline: none / outline: 0', () => {
    // 这条从旧 UI 的 styles.css 继承下来：去掉焦点环而不给替代，
    // 键盘用户就彻底看不见自己在哪。
    const offenders: string[] = []
    for (const path of [...CSS_FILES, ...SOURCE_TS]) {
      if (/outline:\s*(none|0)\b/.test(read(path))) offenders.push(rel(path))
    }
    expect(offenders).toEqual([])
  })

  it('index.html 是 lang="zh-CN"', () => {
    const html = readFileSync(fileURLToPath(new URL('../../index.html', import.meta.url)), 'utf8')
    expect(html).toContain('lang="zh-CN"')
  })

  it('ConfigProvider 挂了 zhCN，dayjs 挂了 zh-cn', () => {
    const main = read(join(SRC, 'main.tsx'))
    expect(main).toContain("from 'antd/locale/zh_CN'")
    expect(main).toContain('locale={zhCN}')
    expect(main).toContain("import 'dayjs/locale/zh-cn'")
    expect(main).toContain("dayjs.locale('zh-cn')")
  })

  it('只做浅色主题，没有 dark mode 的痕迹', () => {
    const offenders: string[] = []
    for (const path of [...CSS_FILES, ...SOURCE_TS]) {
      const text = read(path)
      if (/prefers-color-scheme/.test(text)) offenders.push(`${rel(path)}: prefers-color-scheme`)
      if (/theme\.darkAlgorithm|darkAlgorithm/.test(text)) offenders.push(`${rel(path)}: darkAlgorithm`)
    }
    expect(offenders).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 四、动效
// ─────────────────────────────────────────────────────────────────────────────

describe('动效 2/10', () => {
  const globalCss = readRaw(join(SRC, 'styles/global.css'))

  it('尊重 prefers-reduced-motion', () => {
    expect(globalCss).toContain('prefers-reduced-motion: reduce')
    expect(globalCss).toMatch(/animation-duration:\s*0\.01ms\s*!important/)
    expect(globalCss).toMatch(/transition-duration:\s*0\.01ms\s*!important/)
  })

  it("⛔ 全局禁用平滑滚动：没有 behavior: 'smooth'，也没有 scroll-behavior: smooth", () => {
    // 这不是风格判断。TextCompare.vue:36-38 记录了实测：内嵌 Chromium 里
    // 平滑滚动静默不动，不报错也不滚 —— 而「下一处」失灵时界面看起来完全正常，
    // 人会以为自己已经看完了。
    const offenders: string[] = []
    for (const path of [...CSS_FILES, ...TS_FILES]) {
      const text = read(path)
      if (/scroll-behavior:\s*smooth/.test(text)) offenders.push(`${rel(path)}: css`)
      if (/behavior:\s*['"]smooth['"]/.test(text)) offenders.push(`${rel(path)}: js`)
    }
    expect(offenders).toEqual([])
  })

  it('⛔ 没有装饰性动画：样式表里一个 @keyframes 都没有', () => {
    // 页面入场、列表 stagger、数字滚动、parallax、渐变动画全部禁止。
    const offenders: string[] = []
    for (const path of CSS_FILES) {
      if (/@keyframes/.test(read(path))) offenders.push(rel(path))
    }
    expect(offenders).toEqual([])
  })

  it('过渡时长由 token 给，不各写一个', () => {
    for (const path of MODULE_CSS) {
      const text = read(path)
      const transitions = text.match(/transition:[^;}]*/g) ?? []
      for (const rule of transitions) {
        expect(rule, rel(path)).toContain('var(--rc-motion-duration)')
      }
    }
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 五、本地持久化的边界
// ─────────────────────────────────────────────────────────────────────────────

describe('localStorage 只有一个入口', () => {
  it('除 app/ui-preferences.ts 外没有任何地方直接摸 storage', () => {
    // 边界要靠一个地方守住。散在各处的 localStorage 调用迟早会存进
    // 筛选、草稿或者业务状态 —— 那三样各有各的归宿（URL / 组件 state / 账本）。
    const offenders: string[] = []
    for (const path of TS_FILES) {
      const name = rel(path)
      if (name === 'app/ui-preferences.ts' || name === 'app/ui-preferences.test.ts') continue
      const text = read(path)
      if (/\blocalStorage\b/.test(text)) offenders.push(`${name}: localStorage`)
      if (/\bsessionStorage\b/.test(text)) offenders.push(`${name}: sessionStorage`)
      if (/document\.cookie/.test(text)) offenders.push(`${name}: cookie`)
    }
    expect(offenders).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 六、时区
// ─────────────────────────────────────────────────────────────────────────────

describe('时刻只经过 lib/format.ts', () => {
  it('页面与组件里不出现 toLocaleString / toLocaleDateString', () => {
    // 这台机器在中国。一个"看起来正常"的 toLocaleString 会把 10:00 柏林
    // 显示成 16:00，而柏林排期时刻是这个项目最不能出错的一类数。
    const offenders: string[] = []
    for (const path of SOURCE_TS) {
      const name = rel(path)
      if (name === 'lib/format.ts') continue
      const text = read(path)
      for (const method of ['toLocaleString', 'toLocaleDateString', 'toLocaleTimeString']) {
        if (text.includes(method)) offenders.push(`${name}: ${method}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('展示层不自己 new Date()', () => {
    const offenders: string[] = []
    for (const path of SOURCE_TS) {
      const name = rel(path)
      // format.ts 是唯一允许算时间的地方；http/assert-shape 不碰时间。
      if (name === 'lib/format.ts') continue
      if (/new Date\(/.test(read(path))) offenders.push(name)
    }
    expect(offenders).toEqual([])
  })

  it('Intl.DateTimeFormat 只在 format.ts 里，而且钉死 Asia/Shanghai', () => {
    const format = read(join(SRC, 'lib/format.ts'))
    expect(format).toContain("timeZone: 'Asia/Shanghai'")
    const offenders = SOURCE_TS.filter(
      (path) => rel(path) !== 'lib/format.ts' && /Intl\.DateTimeFormat/.test(read(path)),
    ).map(rel)
    expect(offenders).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 七、禁装清单
// ─────────────────────────────────────────────────────────────────────────────

describe('依赖边界', () => {
  const pkg = JSON.parse(
    readFileSync(fileURLToPath(new URL('../../package.json', import.meta.url)), 'utf8'),
  ) as { dependencies: Record<string, string>; devDependencies: Record<string, string> }

  const installed = { ...pkg.dependencies, ...pkg.devDependencies }

  it.each([
    'tailwindcss',
    'shadcn-ui',
    '@radix-ui/react-dialog',
    'framer-motion',
    'motion',
    'react-hook-form',
    '@ant-design/pro-components',
    'sonner',
    'lucide-react',
    '@playwright/test',
    'jsdom',
    '@testing-library/react',
  ])('没有装 %s', (name) => {
    expect(Object.keys(installed)).not.toContain(name)
  })

  it('Stage A 批准的那几个版本一个没动', () => {
    expect(pkg.dependencies.react).toBe('^19.3.0')
    expect(pkg.dependencies['react-router']).toBe('^7.18.3')
    expect(pkg.dependencies.antd).toBe('^6.6.3')
    expect(pkg.dependencies['@tanstack/react-query']).toBe('^5.102.8')
    expect(pkg.devDependencies.typescript).toBe('^7.0.2')
    expect(pkg.devDependencies.vitest).toBe('^5.0.0')
  })

  it('源码里没有从禁装包 import 的痕迹', () => {
    const offenders: string[] = []
    for (const path of TS_FILES) {
      const text = read(path)
      for (const name of ['tailwind', 'framer-motion', 'react-hook-form', 'lucide-react', 'sonner']) {
        if (new RegExp(`from ['"]${name}`).test(text)) offenders.push(`${rel(path)}: ${name}`)
      }
    }
    expect(offenders).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 八、本版明确不做的东西
// ─────────────────────────────────────────────────────────────────────────────

describe('本版不做的东西，代码里也不许出现', () => {
  it('⛔ 没有 rowSelection（D3：本版不做批量）', () => {
    const offenders = SOURCE_TS.filter((path) => /rowSelection/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('⛔ Table 没有开 virtual（DEF-7：当前数据量不值得）', () => {
    const offenders = SOURCE_TS.filter((path) => /\bvirtual\b/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('⛔ 没有引入 Splitter（DEF-6：正文分栏用 CSS Grid）', () => {
    const offenders = TS_FILES.filter((path) => /\bSplitter\b/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('⛔ 没有历史搜索框（D4：后端没有搜索契约）', () => {
    const offenders = SOURCE_TS.filter((path) => /Input\.Search|Search\b.*antd/.test(read(path))).map(
      rel,
    )
    expect(offenders).toEqual([])
  })
})
