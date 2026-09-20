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

describe('月历完整缓存与部分读取', () => {
  it('部分 Story 独立展示、提示未核实并保留完整缓存时间', () => {
    const base = fixture as unknown as CalendarPayload
    const card = base.cards[0]!
    const data: CalendarPayload = { ...base, status: 'partial', stale: true,
      error: '条目必要字段缺失', refresh_status: 'failed',
      partial_cached_at: '2026-09-04T11:00:00+00:00',
      partial_cards: [{ ...card, placement: 'story', channels: [], caption_status: 'empty',
        rendered: '', read_status: 'incomplete', delivery: 'published' }],
      attempt_coverage: { ...base.coverage, matches_current_month: true, decision_complete: false },
    }
    const text = render(data)
    expect(text).toContain('Story')
    expect(text).toContain('渠道待确认')
    expect(text).toContain('此条尚未核实')
    expect(text).toContain('部分结果 · 不可判断空档')
    expect(text).toContain('上次完整读取：')
    expect(text).toContain('条目必要字段缺失')
    expect(text).not.toContain('本次完整读取的月份中没有内容记录')
  })

  it('读取失败且无完整性证据时不能把空数组显示为完整空月历', () => {
    const base = fixture as unknown as CalendarPayload
    const text = render({ ...base, cards: [], status: 'partial', stale: true,
      error: '尚有未识别内容', refresh_status: 'failed',
      coverage: { ...base.coverage, decision_complete: false },
    })
    expect(text).toContain('尚有未识别内容')
    expect(text).not.toContain('本次完整读取的月份中没有内容记录')
  })
})
