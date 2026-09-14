import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from './queryClient'

import { antdComponents, antdToken } from './theme'
import { routes } from './router'

/**
 * 外壳的集成测试。
 *
 * 跑的是**真实路由表 + 真实 AppShell**，只把 `createBrowserRouter` 换成
 * `createMemoryRouter` —— 所以"路由 → 选中的导航项"这条链是整条验过的，
 * 不是只验中间那个纯函数。
 *
 * 没有 jsdom、没有 testing-library：用 `react-dom/server` 把组件渲染成
 * HTML 字符串再断言。代价是 antd 的浮层（Tooltip / Popover）走 portal，
 * 在 SSR 里不出现 —— 那部分由 Python Playwright 在真实浏览器里核。
 */
function render(path: string): string {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return renderToStaticMarkup(
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{ token: antdToken, components: antdComponents, hashed: false }}
    >
      <AntdApp><QueryClientProvider client={createQueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider></AntdApp>
    </ConfigProvider>,
  )
}

/**
 * 从渲染出来的 href 里读 query。
 *
 * ⚠️ HTML 属性里 `&` 是被转义成 `&amp;` 的，直接丢给 `URLSearchParams`
 * 会把第二个参数读成 `amp;platform`。这一步不是测试的技巧，是真实的 HTML 语义。
 */
function queryOf(href: string): URLSearchParams {
  const [, query = ''] = href.replace(/&amp;/g, '&').split('?')
  return new URLSearchParams(query)
}

/** 取出被选中的那一项菜单的文字。没有选中项时返回 null。 */
function selectedMenuLabel(html: string): string | null {
  const item = /<li class="[^"]*ant-menu-item-selected[^"]*"[^>]*>(.*?)<\/li>/s.exec(html)
  if (!item?.[1]) return null
  const text = item[1].replace(/<[^>]+>/g, '').trim()
  return text === '' ? null : text
}

describe('导航选中项来自路由', () => {
  it.each([
    ['/review', '审校队列'],
    ['/history', '历史归档'],
    ['/calendar', '发布月历'],
    ['/settings', '运营设置'],
  ])('%s 选中「%s」', (path, label) => {
    expect(selectedMenuLabel(render(path))).toBe(label)
  })

  it.each([
    ['/review/fa_neakasaofficial/122100548013379375', '审校队列'],
    ['/history/in_neakasa.tech/3975547640610092585', '历史归档'],
  ])('详情页 %s 仍然选中「%s」', (path, label) => {
    expect(selectedMenuLabel(render(path))).toBe(label)
  })

  it('/runtime 不在主导航里，一项都不选中', () => {
    const html = render('/runtime')
    expect(selectedMenuLabel(html)).toBeNull()
    // 但页面确实打开了运行状态。
    expect(html).toContain('运行状态')
  })

  it('主导航里没有「运行状态」这一项', () => {
    const html = render('/review')
    const menu = /<ul class="[^"]*ant-menu[^"]*"[\s\S]*?<\/ul>/.exec(html)?.[0] ?? ''
    expect(menu).toContain('审校队列')
    expect(menu).toContain('历史归档')
    expect(menu).toContain('发布月历')
    expect(menu).toContain('运营设置')
    expect(menu).not.toContain('运行状态')
  })

  it('顶栏右侧有且只有一个 /runtime 入口', () => {
    const html = render('/review')
    const hrefs = [...html.matchAll(/href="\/runtime[^"]*"/g)]
    expect(hrefs).toHaveLength(1)
  })
})

describe('顶栏', () => {
  it('48px 顶栏里有 DE 标识和「审校台」，没有旧版的两行永久说明', () => {
    const html = render('/review')
    expect(html).toContain('>DE<')
    expect(html).toContain('审校台')
    // 顶栏只保留当前工作区的定位信息。
    expect(html).not.toContain('US 站图文帖')
    expect(html).not.toContain('德国站定时发布')
    expect(html).not.toContain('保存与排期均会留档')
    expect(html).not.toContain('自动排期需完成本机核验')
  })

  it('每个界面恰好一个 h1，内容就是界面名', () => {
    // h1 在正文的工作区头里（app/PageTitle.tsx），不在顶栏 ——
    // 顶栏那 48px 是外壳，回答"我现在在哪"。分工见 AppShell.tsx 的注释。
    for (const [path, title] of [
      ['/review', '审校队列'],
      ['/history', '历史归档'],
      ['/calendar', '发布月历'],
      ['/settings', '运营设置'],
      ['/runtime', '运行状态'],
      ['/review/fa_x/1', '单篇审核'],
      ['/nope', '找不到页面'],
    ] as const) {
      const html = render(path)
      const h1s = [...html.matchAll(/<h1[^>]*>(.*?)<\/h1>/gs)]
      expect(h1s, path).toHaveLength(1)
      expect(h1s[0]?.[1]?.replace(/<[^>]+>/g, ''), path).toBe(title)
    }
  })

  it('顶栏的定位文字和正文的 h1 来自同一个路由 handle，不会不同步', () => {
    const html = render('/history')
    // 一处在顶栏（div），一处在正文（h1），文字必须一致。
    expect(html).toContain('<h1 class')
    const occurrences = html.split('历史归档').length - 1
    // 顶栏 1 次 + 正文 h1 1 次 + 左侧导航 1 次 = 3 次。
    expect(occurrences).toBe(3)
  })

  it('运行状态入口是中性的，不编造「运行正常」', () => {
    const html = render('/review')
    // 状态未知时不能显示绿点或“正常”。
    expect(html).toContain('ant-badge-status-default')
    expect(html).not.toContain('ant-badge-status-success')
    expect(html).not.toContain('运行正常')
  })

  it('折叠按钮带无障碍名字与展开状态', () => {
    const html = render('/review')
    expect(html).toContain('aria-label="收起导航"')
    expect(html).toContain('aria-expanded="true"')
  })
})

describe('详情页面包屑带回来源列表与筛选', () => {
  it('审校详情：面包屑指回队列，且原筛选一个不少', () => {
    const html = render(
      '/review/fa_x/1?queue=processed&platform=facebook&month=2026-07&alerts=1&tab=text',
    )
    const params = queryOf(/href="(\/review\?[^"]*)"/.exec(html)?.[1] ?? '')
    expect(params.get('queue')).toBe('processed')
    expect(params.get('platform')).toBe('facebook')
    expect(params.get('month')).toBe('2026-07')
    expect(params.get('alerts')).toBe('1')
    // tab 是详情自己的参数，返回列表时要摘掉。
    expect(params.get('tab')).toBeNull()
  })

  it('历史详情：面包屑指回历史，页码与页大小都还在', () => {
    const html = render('/history/in_x/2?platform=instagram&month=2026-08&page=2&limit=100&tab=images')
    const params = queryOf(/href="(\/history\?[^"]*)"/.exec(html)?.[1] ?? '')
    expect(params.get('platform')).toBe('instagram')
    expect(params.get('month')).toBe('2026-08')
    expect(params.get('page')).toBe('2')
    expect(params.get('limit')).toBe('100')
    expect(params.get('tab')).toBeNull()
  })

  it('详情上下文只从 URL 来 —— 这次渲染里没有任何 location.state', () => {
    // createMemoryRouter 的初始条目只有一个字符串，没有 state。
    // 上面两条能过，就证明了刷新（history.state === null）之后仍然成立。
    const html = render('/review/fa_x/1?queue=snoozed')
    expect(html).toContain('/review?queue=snoozed')
  })
})

describe('404', () => {
  it('说人话，并且给一个回队列的出口', () => {
    const html = render('/nope/nope')
    expect(html).toContain('这个地址在审校台里没有对应的界面')
    expect(html).toContain('返回审校队列')
    expect(html).toContain('href="/review"')
    // ⛔ 不出现工程语言。
    expect(html).not.toContain('Not Found')
    expect(html).not.toContain('路由')
  })
})

describe('中文语境', () => {
  it('antd 组件走 zh-CN —— 空状态不是英文的 No data', () => {
    const html = render('/nope')
    expect(html).not.toContain('No Data')
    expect(html).not.toContain('No data')
  })

  it('按钮里的两个汉字不被插空格', () => {
    // antd 默认会把「通过」渲染成「通 过」。这是给营销页做的。
    const html = render('/nope')
    expect(html).toContain('>返回审校队列<')
    expect(html).not.toContain('返回审校队 列')
  })
})
