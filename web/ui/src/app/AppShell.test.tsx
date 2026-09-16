import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from './queryClient'

import { antdComponents, antdToken } from './theme'
import { routes } from './router'
import type { ReviewListResponse } from '@/types/domain'
import listFixture from '@/types/__fixtures__/review-list.json'

/** 使用真实路由与 SSR；浮层交互由浏览器测试覆盖。 */
function render(path: string, list?: ReviewListResponse): string {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const client = createQueryClient()
  if (list) client.setQueryData(['tasks', 'review'], list)
  return renderToStaticMarkup(
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{ token: antdToken, components: antdComponents, hashed: false }}
    >
      <AntdApp><QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider></AntdApp>
    </ConfigProvider>,
  )
}

/** 先解码 HTML 属性的 &amp;，再解析查询参数。 */
function queryOf(href: string): URLSearchParams {
  const [, query = ''] = href.replace(/&amp;/g, '&').split('?')
  return new URLSearchParams(query)
}

function selectedMenuLabel(html: string): string | null {
  const item = /<li class="[^"]*ant-menu-item-selected[^"]*"[^>]*>(.*?)<\/li>/s.exec(html)
  if (!item?.[1]) return null
  const text = item[1].replace(/<[^>]+>/g, '').trim()
  return text === '' ? null : text
}

describe('导航选中项来自路由', () => {
  it('平台入口的总数和硬闸由该平台的完整 summary 提供', () => {
    const states = { not_ready: 0, pending_review: 0, edited: 0, snoozed: 0,
      approved: 0, scheduled: 0, skipped: 0, handed_off: 0 }
    const list: ReviewListResponse = { ...(listFixture as unknown as ReviewListResponse), tasks: [],
      summary: { total: 9, with_hard_alerts: 7, tags: [],
        by_status: { ...states, pending_review: 9 },
        by_platform_status: { facebook: { ...states, pending_review: 2 },
          instagram: { ...states, pending_review: 7 } },
        by_platform_hard_alerts: { facebook: 0, instagram: 7 } } }
    const facebook = render('/review/facebook', list).replace(/<[^>]+>/g, '')
    const instagram = render('/review/instagram', list).replace(/<[^>]+>/g, '')
    expect(facebook).toContain('· 2 篇')
    expect(facebook).not.toContain('硬闸')
    expect(instagram).toContain('· 7 篇')
    expect(instagram).toContain('硬闸 7')
    expect(facebook).toContain('Facebook 当前筛选下没有帖子')
    expect(instagram).toContain('Instagram 当前筛选下没有帖子')
  })
  it.each([
    ['/review/facebook', 'Facebook 待审'],
    ['/review/instagram', 'Instagram 待审'],
    ['/history', '历史归档'],
    ['/calendar', '发布月历'],
    ['/settings', '运营设置'],
  ])('%s 选中「%s」', (path, label) => {
    expect(selectedMenuLabel(render(path))).toBe(label)
  })

  it.each([
    ['/review/fa_neakasaofficial/122100548013379375?platform=facebook', 'Facebook 待审'],
    ['/review/in_x/1?platform=instagram', 'Instagram 待审'],
    ['/history/in_neakasa.tech/3975547640610092585', '历史归档'],
  ])('详情页 %s 仍然选中「%s」', (path, label) => {
    expect(selectedMenuLabel(render(path))).toBe(label)
  })

  it('/runtime 不在主导航里，一项都不选中', () => {
    const html = render('/runtime')
    expect(selectedMenuLabel(html)).toBeNull()
    expect(html).toContain('运行状态')
  })

  it('主导航里没有「运行状态」这一项', () => {
    const html = render('/review/facebook')
    const menu = /<ul class="[^"]*ant-menu[^"]*"[\s\S]*?<\/ul>/.exec(html)?.[0] ?? ''
    expect(menu).toContain('Facebook 待审')
    expect(menu).toContain('Instagram 待审')
    expect(menu).toContain('历史归档')
    expect(menu).toContain('发布月历')
    expect(menu).toContain('运营设置')
    expect(menu).not.toContain('运行状态')
  })

  it('顶栏右侧有且只有一个 /runtime 入口', () => {
    const html = render('/review/facebook')
    const hrefs = [...html.matchAll(/href="\/runtime[^"]*"/g)]
    expect(hrefs).toHaveLength(1)
  })
})

describe('顶栏', () => {
  it('48px 顶栏里有 DE 标识和「审校台」，没有旧版的两行永久说明', () => {
    const html = render('/review/facebook')
    expect(html).toContain('>DE<')
    expect(html).toContain('审校台')
    expect(html).not.toContain('US 站图文帖')
    expect(html).not.toContain('德国站定时发布')
    expect(html).not.toContain('保存与排期均会留档')
    expect(html).not.toContain('自动排期需完成本机核验')
  })

  it('每个界面恰好一个 h1，内容就是界面名', () => {
    for (const [path, title] of [
      ['/review/facebook', 'Facebook 待审'],
      ['/review/instagram', 'Instagram 待审'],
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
    expect(html).toContain('<h1 class')
    const occurrences = html.split('历史归档').length - 1
    expect(occurrences).toBe(3)
  })

  it('运行状态入口是中性的，不编造「运行正常」', () => {
    const html = render('/review/facebook')
    expect(html).toContain('ant-badge-status-default')
    expect(html).not.toContain('ant-badge-status-success')
    expect(html).not.toContain('运行正常')
  })

  it('折叠按钮带无障碍名字与展开状态', () => {
    const html = render('/review/facebook')
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
    // 不提供 location.state，验证仅凭 URL 恢复上下文。
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
    const html = render('/nope')
    expect(html).toContain('>返回审校队列<')
    expect(html).not.toContain('返回审校队 列')
  })
})
