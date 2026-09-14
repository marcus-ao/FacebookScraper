import { describe, expect, it } from 'vitest'
import { QueryObserver } from '@tanstack/react-query'
import { createQueryClient } from '@/app/queryClient'
import { parseReviewListQuery } from '@/app/search-params'
import { reviewListOptions } from '@/hooks/useTasks'
import { decisionBody } from '@/services/review'
import { filterReviewRows, patchReviewList, queueCounts } from './model'
import type { DisplayStatus, ReviewListResponse, TaskDetail } from '@/types/domain'
import listFixture from '@/types/__fixtures__/review-list.json'
import detailFixture from '@/types/__fixtures__/task-detail-active.json'

const data = listFixture as unknown as ReviewListResponse
const detail = detailFixture as unknown as TaskDetail
const statuses: DisplayStatus[] = ['not_ready','pending_review','edited','snoozed','approved','scheduled','skipped','handed_off']

describe('review query 与行补丁', () => {
  it('四桶按真实状态筛选，待我审为空时不会换桶', () => {
    const rows = statuses.map(status => ({ ...data.tasks[0]!, status }))
    for (const [bucket, expected] of Object.entries({ review: ['pending_review','edited'], not_ready:['not_ready'], snoozed:['snoozed'], processed:['approved','scheduled','skipped','handed_off'] })) {
      expect(filterReviewRows(rows, parseReviewListQuery({ queue: bucket })).map(row => row.status)).toEqual(expected)
    }
    expect(filterReviewRows(rows.filter(row => row.status === 'not_ready'), parseReviewListQuery({}))).toEqual([])
  })
  it('平台/月/分类/硬闸是同一全量响应的前端过滤', () => {
    const row = { ...data.tasks[0]!, status: 'pending_review' as const, platform:'facebook' as const, month:'2026-09', tags:['Riko'], hard_alerts:[{code:'unknown_collaborator',label:'第三方作者'}] }
    expect(filterReviewRows([row], parseReviewListQuery({ platform:'facebook',month:'2026-09',tag:'Riko',alerts:'1' }))).toHaveLength(1)
    expect(filterReviewRows([row], parseReviewListQuery({ tag:'__untagged__' }))).toHaveLength(0)
  })
  it('计数使用 summary，不取当前 visible rows', () => {
    const summary = { ...data.summary, by_status:{ pending_review:12, edited:3, not_ready:7, snoozed:4, approved:1, scheduled:2, skipped:3, handed_off:4 } }
    expect(queueCounts(summary)).toEqual({review:15,not_ready:7,snoozed:4,processed:10})
  })
  it('详情 mutation 就地更新行和计数，人工优先且不修改原缓存', () => {
    const previous = { ...data.tasks[0]!, id: detail.id, status:'pending_review' as const }
    const input = { ...data, tasks:[previous], summary:{...data.summary,by_status:{not_ready:0,edited:0,snoozed:0,approved:0,scheduled:0,skipped:0,handed_off:0,pending_review:1}} }
    const changed = {...detail, status:'edited' as const,text:{...detail.text,de_human:'人工优先',de_machine:'机器'}}
    const next = patchReviewList(input, changed)
    expect(next.tasks[0]?.text_de_excerpt).toBe('人工优先')
    expect(next.summary.by_status).toMatchObject({pending_review:0,edited:1})
    expect(input.tasks[0]?.status).toBe('pending_review')
    expect(patchReviewList(next, changed).summary.by_status).toEqual(next.summary.by_status)
  })
  it('即使 staleTime=0，卸载后重新订阅 review query 也不发 GET', async () => {
    const client = createQueryClient()
    let calls = 0
    const options = { ...reviewListOptions(), queryFn: async () => { calls++; return data } }
    await client.fetchQuery(options)
    for(let index=0; index<3; index++) {
      const observer = new QueryObserver(client, options)
      const unsubscribe = observer.subscribe(() => {})
      await Promise.resolve(); unsubscribe()
    }
    expect(calls).toBe(1)
    expect(options.queryKey).toEqual(['tasks','review'])
    client.clear()
  })
  it('挂起请求保留 source/review，时间明确 +08:00；export 保留旧 body keys', () => {
    const form = {action:'snoozed' as const, reason:'等待',wakeAt:'2026-10-25T10:15',handoffUrl:''}
    expect(decisionBody(detail, form)).toEqual({source_text_sha256:detail.text.source_text_sha256,review_revision:detail.review.revision,action:'snoozed',reason:'等待',wake_at:'2026-10-25T10:15:00+08:00',handoff_url:''})
    expect(Object.keys(decisionBody(data.tasks[0]!, {...form, action:'export',wakeAt:''})).sort()).toEqual(['action','handoff_url','reason','review_revision','source_text_sha256'])
  })
})
