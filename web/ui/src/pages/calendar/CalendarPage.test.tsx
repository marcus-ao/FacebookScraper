import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from '@/app/queryClient'
import { routes } from '@/app/router'
import type { CalendarPayload } from '@/types/domain'
import fixture from '@/types/__fixtures__/calendar.json'

function render(data: CalendarPayload) {
  const client = createQueryClient()
  client.setQueryData(['calendar'], data)
  return renderToStaticMarkup(<QueryClientProvider client={client}>
    <RouterProvider router={createMemoryRouter(routes, { initialEntries: ['/calendar'] })} />
  </QueryClientProvider>).replace(/<[^>]+>/g, '')
}

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
    expect(text).toContain('未读全')
    // 卡片不再堆叠 jargon 与占位行；未读卡的渠道/位置/形式行全部不显示。
    expect(text).not.toContain('明细未读取')
    expect(text).not.toContain('渠道未读取')
    expect(text).not.toContain('位置待识别')
    expect(text).not.toContain('形式待核实')
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
