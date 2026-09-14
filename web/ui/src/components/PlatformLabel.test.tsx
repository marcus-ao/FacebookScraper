import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { PlatformLabel } from './PlatformLabel'

const html = (node: React.ReactElement) => renderToStaticMarkup(node)

describe('PlatformLabel', () => {
  it('两个平台各一种写法，全应用统一', () => {
    // 两个平台各有一个统一的显示名称。
    expect(html(<PlatformLabel platform="facebook" />)).toContain('Facebook')
    expect(html(<PlatformLabel platform="instagram" />)).toContain('Instagram')
  })

  it('图标 + 文字，两样都在', () => {
    const markup = html(<PlatformLabel platform="facebook" />)
    expect(markup).toContain('anticon-facebook')
    expect(markup).toContain('Facebook')
  })

  it('带 data-platform，给浏览器级断言与列样式用', () => {
    expect(html(<PlatformLabel platform="instagram" />)).toContain('data-platform="instagram"')
  })

  it('iconOnly 时文字进 aria-label 与 title，不是消失', () => {
    const markup = html(<PlatformLabel platform="facebook" iconOnly />)
    expect(markup).toContain('aria-label="Facebook"')
    expect(markup).toContain('title="Facebook"')
    // 可见文本没了，但读屏与悬停都还拿得到。
    expect(markup.replace(/<[^>]+>/g, '').trim()).toBe('')
  })

  it('装饰性图标对读屏隐藏', () => {
    // 文字已经说了是哪个平台，图标再读一遍是噪声。
    expect(html(<PlatformLabel platform="facebook" />)).toContain('aria-hidden="true"')
  })

  it('⛔ 不带品牌色 —— 内容区只有红黄两种饱和色', () => {
    for (const markup of [
      html(<PlatformLabel platform="facebook" />),
      html(<PlatformLabel platform="instagram" />),
    ]) {
      // 图标走 currentColor，不该有任何写死的颜色。
      expect(markup).not.toMatch(/fill="#/)
      expect(markup).not.toMatch(/style="[^"]*color:\s*#/)
      expect(markup).toContain('fill="currentColor"')
    }
  })
})
