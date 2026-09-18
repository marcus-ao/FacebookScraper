import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { antdToken, cssVariables } from './theme'

/** 检查设计 token、可访问性及组件约束。 */

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

const SELF = 'app/design-discipline.test.ts'

/** 扫描代码前去注释，保留 URL 中的 //。 */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:\w])\/\/[^\n]*/g, '$1')
}

const read = (path: string) => stripComments(readFileSync(path, 'utf8'))
const readRaw = (path: string) => readFileSync(path, 'utf8')

const SCANNED = ALL_FILES.filter((path) => rel(path) !== SELF)
const CSS_FILES = SCANNED.filter((path) => path.endsWith('.css'))
const MODULE_CSS = CSS_FILES.filter((path) => path.endsWith('.module.css'))
const TS_FILES = SCANNED.filter((path) => /\.tsx?$/.test(path))
const SOURCE_TS = TS_FILES.filter((path) => !path.includes('.test.'))


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
      if (['app/theme.ts', 'app/theme.test.ts', 'lib/contrast.test.ts'].includes(name)) continue
      const hits = read(path).match(/(['"`])#[0-9a-fA-F]{3,8}\1/g)
      if (hits) offenders.push(`${name}: ${hits.join(', ')}`)
    }
    expect(offenders).toEqual([])
  })
})


describe('字号、间距、圆角只走语义变量', () => {
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
    for (const path of MODULE_CSS) {
      expect(read(path), rel(path)).toContain('var(--rc-')
    }
  })
})


describe('无障碍底线', () => {
  const globalCss = readRaw(join(SRC, 'styles/global.css'))

  it('有全局 focus-visible，2px 主色 + 2px offset', () => {
    expect(globalCss).toContain(':focus-visible')
    expect(globalCss).toMatch(
      /outline:\s*var\(--rc-focus-w\)\s+solid\s+var\(--rc-primary\)\s*!important/,
    )
    expect(globalCss).toMatch(/outline-offset:\s*var\(--rc-focus-offset\)\s*!important/)
    expect(cssVariables['--rc-focus-w']).toBe('2px')
    expect(cssVariables['--rc-focus-offset']).toBe('2px')
  })

  it('焦点环压得过 antd 那八条组件级规则', () => {
    // 全局焦点环须覆盖组件默认 offset。
    expect(globalCss).toContain('html:root :focus-visible')
    expect(globalCss).toContain('!important')
  })

  it('antd 自己画的焦点宽度也是 2px', () => {
    expect(antdToken.lineWidthFocus).toBe(2)
  })

  it('⛔ 任何地方都没有 outline: none / outline: 0', () => {
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


describe('动效 2/10', () => {
  const globalCss = readRaw(join(SRC, 'styles/global.css'))

  it('尊重 prefers-reduced-motion', () => {
    expect(globalCss).toContain('prefers-reduced-motion: reduce')
    expect(globalCss).toMatch(/animation-duration:\s*0\.01ms\s*!important/)
    expect(globalCss).toMatch(/transition-duration:\s*0s\s*!important/)
  })

  it("⛔ 全局禁用平滑滚动：没有 behavior: 'smooth'，也没有 scroll-behavior: smooth", () => {
    const offenders: string[] = []
    for (const path of [...CSS_FILES, ...TS_FILES]) {
      const text = read(path)
      if (/scroll-behavior:\s*smooth/.test(text)) offenders.push(`${rel(path)}: css`)
      if (/behavior:\s*['"]smooth['"]/.test(text)) offenders.push(`${rel(path)}: js`)
    }
    expect(offenders).toEqual([])
  })

  it('⛔ 没有装饰性动画：样式表里一个 @keyframes 都没有', () => {
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


describe('localStorage 只有一个入口', () => {
  it('除 app/ui-preferences.ts 外没有任何地方直接摸 storage', () => {
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


describe('时刻只经过 lib/format.ts', () => {
  it('页面与组件里不出现 toLocaleString / toLocaleDateString', () => {
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

  it('依赖版本保持在受支持的组合', () => {
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


describe('界面能力边界', () => {
  it('不显示批量勾选', () => {
    const offenders = SOURCE_TS.filter((path) => /rowSelection/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('表格保持普通分页', () => {
    const offenders = SOURCE_TS.filter((path) => /\bvirtual\b/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('正文使用固定分栏', () => {
    const offenders = TS_FILES.filter((path) => /\bSplitter\b/.test(read(path))).map(rel)
    expect(offenders).toEqual([])
  })

  it('不显示未提供接口的历史搜索', () => {
    const offenders = SOURCE_TS.filter((path) => /Input\.Search|Search\b.*antd/.test(read(path))).map(
      rel,
    )
    expect(offenders).toEqual([])
  })
})


describe('焦点与可访问名', () => {
  const globalCss = readRaw(join(SRC, 'styles/global.css'))

  it('加载结束后隐藏残留图标', () => {
    // 快速结束的 loading 须移除离场图标，避免污染按钮可访问名。
    expect(globalCss).toContain('.ant-btn-loading-icon-motion-leave')
    expect(globalCss).toMatch(/\.ant-btn-loading-icon-motion-leave\s*\{[^}]*display:\s*none\s*!important/)
  })

  it('月历保留有效的无障碍结构', () => {
    const calendar = read(join(SRC, 'pages/calendar/CalendarPage.tsx'))
    expect(calendar).not.toMatch(/role=["']list["']/)
    expect(calendar).not.toMatch(/role=["']listitem["']/)
    expect(calendar).toContain('data-day')
  })
})
