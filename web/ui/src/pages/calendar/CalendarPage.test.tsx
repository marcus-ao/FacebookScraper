import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/queryClient'
import { routes } from '@/app/router'
import type { CalendarCard, CalendarPayload } from '@/types/domain'
import { CardDetail } from './CalendarPage'
import fixture from '@/types/__fixtures__/calendar.json'

function render(data: CalendarPayload) {
  const client = createQueryClient()
  client.setQueryData(['calendar'], data)
  return renderToStaticMarkup(<QueryClientProvider client={client}>
    <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/calendar'] })} />
  </QueryClientProvider>).replace(/<[^>]+>/g, '')
}

const detailText = (card: CalendarCard) =>
  renderToStaticMarkup(<CardDetail card={card} />).replace(/<[^>]+>/g, '')

describe('点击后的详情', () => {
  const card = () => (fixture as unknown as CalendarPayload).cards[0]!

  it('时刻、账号和原帖链接并列，正文在下面', () => {
    const text = detailText({ ...card(), accounts: { instagram: 'neakasa.global' },
      permalinks: { instagram: 'https://www.instagram.com/p/AbCdEf' } })
    expect(text).toContain('2026-09-04 18:39')
    expect(text).toContain('neakasa.global')
    expect(text).toContain('查看原帖')
    expect(text).toContain(card().rendered)
    expect(text).not.toContain('系统观测到的后台记录')
  })

  it('两个渠道各给一条原帖链接', () => {
    const markup = renderToStaticMarkup(<CardDetail card={{ ...card(),
      channels: ['facebook', 'instagram'],
      permalinks: { facebook: 'https://www.facebook.com/permalink.php?story_fbid=1',
        instagram: 'https://www.instagram.com/p/AbCdEf' } }} />)
    expect(markup).toContain('查看 Facebook 原帖')
    expect(markup).toContain('查看 Instagram 原帖')
    expect(markup).toContain('href="https://www.instagram.com/p/AbCdEf"')
  })

  it('没有公开地址就不显示按钮；没有正文改成复核提示', () => {
    const text = detailText({ ...card(), rendered: '', caption_status: 'empty', permalinks: {} })
    expect(text).toContain('该篇帖子不含文本部分,请跳转原帖进行复核确认')
    expect(text).not.toContain('查看原帖')
    expect(text).not.toContain('正文尚未核实')
  })

  it('明细没读出来时，提示在详情里', () => {
    const text = detailText({ ...card(), channels: [], rendered: '', read_status: 'incomplete', delivery: 'scheduled' })
    expect(text).toContain('该篇帖子的具体信息尚未成功获取，请前往Meta后台任务日历进行人工复核确认')
  })
})

describe('月历同步展示', () => {
  it('明细未读取的卡片照常占位，状态短签加警告', () => {
    const base = fixture as unknown as CalendarPayload
    const card = base.cards[0]!
    const data: CalendarPayload = { ...base, status: 'ready', stale: false, error: null,
      refresh_status: 'refreshed',
      cards: [{ ...card, placement: 'unknown', channels: [], caption_status: 'unknown',
        rendered: '', read_status: 'incomplete', delivery: 'scheduled' }],
      coverage: { ...base.coverage, matches_current_month: true, decision_complete: false,
        unresolved_count: 1 },
    }
    const text = render(data)
    expect(text).toContain('定时')
    // 没读出来的说明在详情里，不占格子。
    expect(text).not.toContain('未读全')
    expect(text).not.toContain('明细未读取')
    expect(text).not.toContain('渠道未读取')
    expect(text).not.toContain('位置待识别')
    expect(text).not.toContain('形式待核实')
    expect(text).not.toContain('系统观测到的后台记录')
    // 两条读不出明细的卡片不再把整月染成过期。
    expect(text).not.toContain('数据可能已过期')
    expect(text).not.toContain('本次读取的月份中没有内容记录')
    // 受众钟点与时区字眼不再出现在页面上。
    expect(text).not.toContain('德国')
    expect(text).not.toContain('北京')
  })

  it('读取失败时保留上次数据并说明失败，不把空数组显示为空月历', () => {
    const base = fixture as unknown as CalendarPayload
    const text = render({ ...base, cards: [], status: 'stale', stale: true,
      error: '未能读取发布日历，请检查发布浏览器后重试。', refresh_status: 'failed',
    })
    expect(text).toContain('未能读取发布日历')
    expect(text).not.toContain('本次读取的月份中没有内容记录')
  })

  it('覆盖范围仍有缺口时不把空卡片数组宣称为空月份', () => {
    const base = fixture as unknown as CalendarPayload
    const text = render({ ...base, cards: [], status: 'ready', stale: false,
      coverage: { ...base.coverage, matches_current_month: true, decision_complete: false },
    })
    expect(text).not.toContain('本次读取的月份中没有内容记录')
  })
})
