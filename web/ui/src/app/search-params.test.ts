import { describe, expect, it } from 'vitest'

import {
  DEFAULT_HISTORY_LIMIT,
  DEFAULT_QUEUE,
  HISTORY_PAGE_SIZES,
  QUEUE_BUCKETS,
  QUEUE_BUCKET_STATUSES,
  bucketOf,
  buildDetailSearch,
  buildListSearch,
  legacyRedirect,
  parseMonth,
  parsePlatform,
  parseTag,
  parseHistoryListQuery,
  parseQueue,
  parseReviewListQuery,
  pickContext,
} from './search-params'
import type { DisplayStatus } from '@/types/domain'


describe('队列分桶：参数叫 queue，不叫 status', () => {
  it('四个分桶', () => {
    expect(QUEUE_BUCKETS).toEqual(['review', 'not_ready', 'snoozed', 'processed'])
  })

  it('分桶覆盖全部八个展示态，且互不重叠', () => {
    const all = QUEUE_BUCKETS.flatMap((bucket) => [...QUEUE_BUCKET_STATUSES[bucket]])
    expect(new Set(all).size).toBe(all.length)
    expect(all.sort()).toEqual(
      [
        'approved',
        'edited',
        'handed_off',
        'not_ready',
        'pending_review',
        'scheduled',
        'skipped',
        'snoozed',
      ].sort(),
    )
  })

  it('「待我审」= pending_review + edited', () => {
    expect(QUEUE_BUCKET_STATUSES.review).toEqual(['pending_review', 'edited'])
  })

  it('「已处理」是四个终态/提交中态的 UI 分桶，不改底层语义', () => {
    expect(QUEUE_BUCKET_STATUSES.processed).toEqual([
      'approved',
      'scheduled',
      'skipped',
      'handed_off',
    ])
  })

  it('not_ready 自己一个分桶，不回塞「待我审」', () => {
    expect(QUEUE_BUCKET_STATUSES.not_ready).toEqual(['not_ready'])
    expect(QUEUE_BUCKET_STATUSES.review).not.toContain('not_ready')
  })

  it('bucketOf 把真实状态映射回分桶', () => {
    const cases: [DisplayStatus, string][] = [
      ['pending_review', 'review'],
      ['edited', 'review'],
      ['not_ready', 'not_ready'],
      ['snoozed', 'snoozed'],
      ['approved', 'processed'],
      ['scheduled', 'processed'],
      ['skipped', 'processed'],
      ['handed_off', 'processed'],
    ]
    for (const [status, bucket] of cases) expect(bucketOf(status)).toBe(bucket)
  })

  it('parseQueue：缺省与未知值回落 review', () => {
    expect(parseQueue('processed')).toBe('processed')
    expect(parseQueue(null)).toBe(DEFAULT_QUEUE)
    expect(parseQueue(undefined)).toBe(DEFAULT_QUEUE)
    expect(parseQueue('')).toBe(DEFAULT_QUEUE)
    expect(parseQueue('pending_review')).toBe(DEFAULT_QUEUE)
    expect(parseQueue('nonsense')).toBe(DEFAULT_QUEUE)
  })
})

describe('详情 URL 携带来源列表上下文', () => {
  const reviewParams = new URLSearchParams({
    queue: 'processed',
    platform: 'facebook',
    month: '2026-07',
    tag: 'Riko',
    alerts: '1',
    somethingElse: 'x',
  })

  it('从审校队列带走 queue/platform/month/tag/alerts', () => {
    const picked = pickContext(reviewParams, 'review')
    expect([...picked.keys()].sort()).toEqual(['alerts', 'month', 'platform', 'queue', 'tag'])
    expect(picked.get('queue')).toBe('processed')
  })

  it('白名单外的参数不带走', () => {
    expect(pickContext(reviewParams, 'review').has('somethingElse')).toBe(false)
  })

  it('从历史带走 platform/month/tag/page/limit，不带 queue', () => {
    const params = new URLSearchParams({
      platform: 'instagram',
      month: '2026-08',
      page: '2',
      limit: '50',
      queue: 'review',
    })
    const picked = pickContext(params, 'history')
    expect([...picked.keys()].sort()).toEqual(['limit', 'month', 'page', 'platform'])
    expect(picked.has('queue')).toBe(false)
  })

  it('空串与缺失一律丢掉 —— platform= 这种空参数会让后端 422', () => {
    const params = new URLSearchParams({ platform: '', month: '2026-07' })
    const picked = pickContext(params, 'review')
    expect(picked.has('platform')).toBe(false)
    expect(picked.get('month')).toBe('2026-07')
  })

  it('buildDetailSearch 在上下文之外加上 tab', () => {
    const search = buildDetailSearch(reviewParams, 'review', { tab: 'images' })
    const parsed = new URLSearchParams(search)
    expect(parsed.get('tab')).toBe('images')
    expect(parsed.get('queue')).toBe('processed')
  })

  it('buildListSearch 把 tab 摘掉，其余原样带回 —— 这就是「返回列表恢复筛选」', () => {
    const detail = new URLSearchParams(buildDetailSearch(reviewParams, 'review', { tab: 'images' }))
    const back = new URLSearchParams(buildListSearch(detail, 'review'))
    expect(back.has('tab')).toBe(false)
    expect(back.get('queue')).toBe('processed')
    expect(back.get('month')).toBe('2026-07')
    expect(back.get('alerts')).toBe('1')
  })

  it('历史往返保住 page 与 limit', () => {
    const list = new URLSearchParams({ platform: 'instagram', page: '3', limit: '100' })
    const detail = new URLSearchParams(buildDetailSearch(list, 'history', { tab: 'text' }))
    const back = new URLSearchParams(buildListSearch(detail, 'history'))
    expect(back.get('page')).toBe('3')
    expect(back.get('limit')).toBe('100')
  })

  it('往返是幂等的 —— 来回切换不会丢参数也不会长出参数', () => {
    const once = buildListSearch(reviewParams, 'review')
    const twice = buildListSearch(new URLSearchParams(once), 'review')
    expect(twice).toBe(once)
  })

  it('也接受普通对象（详情页拿到的可能是 Record 而不是 URLSearchParams）', () => {
    const picked = pickContext({ queue: 'snoozed', platform: null, month: '2026-07' }, 'review')
    expect(picked.get('queue')).toBe('snoozed')
    expect(picked.has('platform')).toBe(false)
  })
})

describe('列表查询参数解析（详情页要用同一份来算 n / N）', () => {
  it('审校队列', () => {
    const query = parseReviewListQuery(
      new URLSearchParams({ queue: 'not_ready', platform: 'facebook', alerts: '1' }),
    )
    expect(query).toEqual({
      queue: 'not_ready',
      platform: 'facebook',
      month: null,
      tag: null,
      alerts: true,
    })
  })

  it('审校队列：空参数变 null，alerts 只认 "1"', () => {
    const query = parseReviewListQuery(new URLSearchParams({ platform: '', alerts: 'true' }))
    expect(query.platform).toBeNull()
    expect(query.alerts).toBe(false)
    expect(query.queue).toBe('review')
  })

  it('历史：默认 50，可选 20/50/100', () => {
    expect(HISTORY_PAGE_SIZES).toEqual([20, 50, 100])
    expect(parseHistoryListQuery(new URLSearchParams()).limit).toBe(DEFAULT_HISTORY_LIMIT)
    expect(parseHistoryListQuery(new URLSearchParams({ limit: '20' })).limit).toBe(20)
    expect(parseHistoryListQuery(new URLSearchParams({ limit: '100' })).limit).toBe(100)
  })

  it('历史：不在白名单里的 limit 回落 50', () => {
    expect(parseHistoryListQuery(new URLSearchParams({ limit: '30' })).limit).toBe(50)
    expect(parseHistoryListQuery(new URLSearchParams({ limit: '9999' })).limit).toBe(50)
    expect(parseHistoryListQuery(new URLSearchParams({ limit: 'abc' })).limit).toBe(50)
  })

  it('历史：page 至少是 1', () => {
    expect(parseHistoryListQuery(new URLSearchParams({ page: '0' })).page).toBe(1)
    expect(parseHistoryListQuery(new URLSearchParams({ page: '-3' })).page).toBe(1)
    expect(parseHistoryListQuery(new URLSearchParams({ page: '2.5' })).page).toBe(1)
    expect(parseHistoryListQuery(new URLSearchParams({ page: '7' })).page).toBe(7)
  })
})

describe('旧 URL 永久兼容（DECISION_LOG）', () => {
  it('?task= 带斜杠的 id 拆成两段路由参数', () => {
    expect(legacyRedirect('?task=fa_neakasaofficial/122100548013379375')).toBe(
      '/review/fa_neakasaofficial/122100548013379375',
    )
  })

  it('冻结账号的历史 id 同样可用', () => {
    expect(legacyRedirect('?task=in_neakasa.tech/3975547640610092585')).toBe(
      '/review/in_neakasa.tech/3975547640610092585',
    )
  })

  it('?view=history&task= 进历史那条详情路由 —— 返回时才会回到历史', () => {
    expect(legacyRedirect('?view=history&task=in_neakasa.tech/3975547640610092585')).toBe(
      '/history/in_neakasa.tech/3975547640610092585',
    )
  })

  it('四个 view 各自映射', () => {
    expect(legacyRedirect('?view=history')).toBe('/history')
    expect(legacyRedirect('?view=calendar')).toBe('/calendar')
    expect(legacyRedirect('?view=settings')).toBe('/settings')
    expect(legacyRedirect('?view=runtime')).toBe('/runtime')
  })

  it('未知 view 回落审校队列', () => {
    expect(legacyRedirect('?view=nonsense')).toBe('/review')
    expect(legacyRedirect('?view=')).toBe('/review')
  })

  it('task 形状不对时退回列表，不造一个坏详情 URL', () => {
    expect(legacyRedirect('?task=no-slash')).toBe('/review')
    expect(legacyRedirect('?task=/leading')).toBe('/review')
    expect(legacyRedirect('?task=trailing/')).toBe('/review')
  })

  it('不是旧形状时返回 null，交给正常路由', () => {
    expect(legacyRedirect('')).toBeNull()
    expect(legacyRedirect('?queue=processed')).toBeNull()
    expect(legacyRedirect('?tab=images')).toBeNull()
  })

  it('id 里的特殊字符被编码，斜杠仍然是路径分隔', () => {
    const result = legacyRedirect('?task=fa_x/a b')
    expect(result).toBe('/review/fa_x/a%20b')
    expect(result?.split('/').filter(Boolean)).toHaveLength(3)
  })
})

describe('手改坏的 URL 不要甩一个 422 给她', () => {
  it('platform 只认后端 pattern 里的那两个', () => {
    expect(parsePlatform('facebook')).toBe('facebook')
    expect(parsePlatform('instagram')).toBe('instagram')
    expect(parsePlatform('facebookk')).toBeNull()
    expect(parsePlatform('FACEBOOK')).toBeNull()
    expect(parsePlatform('tiktok')).toBeNull()
    expect(parsePlatform(null)).toBeNull()
  })

  it('month 认 YYYY-MM，也认归档里真实存在的 undated', () => {
    expect(parseMonth('2026-09')).toBe('2026-09')
    expect(parseMonth('2026-01')).toBe('2026-01')
    expect(parseMonth('2026-12')).toBe('2026-12')
    expect(parseMonth('undated')).toBe('undated')
  })

  it('month 不认不存在的月份和别的形状', () => {
    expect(parseMonth('2026-99')).toBeNull()
    expect(parseMonth('2026-00')).toBeNull()
    expect(parseMonth('2026-13')).toBeNull()
    expect(parseMonth('2026-9')).toBeNull()
    expect(parseMonth('2026-09-14')).toBeNull()
    expect(parseMonth('hello')).toBeNull()
  })

  it('⛔ tag 是自由业务标签，这里不许编白名单', () => {
    expect(parseTag('Riko')).toBe('Riko')
    expect(parseTag('促销')).toBe('促销')
    expect(parseTag('#Katzen 2026')).toBe('#Katzen 2026')
    expect(parseTag('__untagged__')).toBe('__untagged__')
    expect(parseTag('  ')).toBeNull()
    expect(parseTag('')).toBeNull()
  })

  it('列表查询把非法值当成"没筛"，不原样发出去', () => {
    const review = parseReviewListQuery({ queue: 'review', platform: 'tiktok', month: '2026-99', tag: ' ' })
    expect(review.platform).toBeNull()
    expect(review.month).toBeNull()
    expect(review.tag).toBeNull()
    const history = parseHistoryListQuery({ platform: 'facebookk', month: 'hello', page: '2' })
    expect(history.platform).toBeNull()
    expect(history.month).toBeNull()
    expect(history.page).toBe(2)
  })

  it('详情 URL 里也不再带着坏值往下传', () => {
    const search = buildDetailSearch({ queue: 'review', platform: 'tiktok', month: '2026-99', tag: 'Riko' }, 'review', { tab: 'images' })
    expect(search).not.toContain('tiktok')
    expect(search).not.toContain('2026-99')
    expect(search).toContain('queue=review')
    expect(search).toContain('tag=Riko')
    expect(search).toContain('tab=images')
  })

  it('合法值原样通过，不会被"顺手规范化"成别的东西', () => {
    const search = buildListSearch({ platform: 'instagram', month: '2026-09', tag: '__untagged__', page: '3', limit: '20' }, 'history')
    expect(new URLSearchParams(search).get('platform')).toBe('instagram')
    expect(new URLSearchParams(search).get('month')).toBe('2026-09')
    expect(new URLSearchParams(search).get('tag')).toBe('__untagged__')
    expect(new URLSearchParams(search).get('page')).toBe('3')
  })
})
