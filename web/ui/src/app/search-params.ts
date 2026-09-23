/** 列表与详情共用 URL 参数，刷新后仍能恢复筛选、页码和相邻帖子。 */

import type { DisplayStatus, Platform, QueueBucket } from '@/types/domain'

export const QUEUE_BUCKETS = ['review', 'not_ready', 'snoozed', 'processed'] as const

/** queue 分桶包含多个真实 status，不能直接作为接口 status 传入。 */
export const QUEUE_BUCKET_STATUSES: Readonly<Record<QueueBucket, readonly DisplayStatus[]>> = {
  // 冻结的帖子还等着人选时刻，留在待审里；挪进「已处理」就没人再看它了。
  review: ['pending_review', 'edited', 'content_locked'],
  not_ready: ['not_ready'],
  snoozed: ['snoozed'],
  processed: ['approved', 'scheduled', 'skipped', 'handed_off'],
}

export const QUEUE_BUCKET_LABEL: Readonly<Record<QueueBucket, string>> = {
  review: '待我审',
  not_ready: '未就绪',
  snoozed: '已挂起',
  processed: '已处理',
}

export const DEFAULT_QUEUE: QueueBucket = 'review'

export const HISTORY_PAGE_SIZES = [20, 50, 100] as const
export const DEFAULT_HISTORY_LIMIT = 50

const isQueueBucket = (value: string | null): value is QueueBucket =>
  value !== null && (QUEUE_BUCKETS as readonly string[]).includes(value)

export function parseQueue(value: string | null | undefined): QueueBucket {
  return isQueueBucket(value ?? null) ? (value as QueueBucket) : DEFAULT_QUEUE
}

// platform 限两平台；month 兼容 undated；tag 保留自由取值与 __untagged__。

const PLATFORMS: readonly Platform[] = ['facebook', 'instagram']
const MONTH = /^\d{4}-(?:0[1-9]|1[0-2])$/

export function parsePlatform(value: string | null | undefined): Platform | null {
  return PLATFORMS.find(platform => platform === value) ?? null
}

export function parseMonth(value: string | null | undefined): string | null {
  if (!value) return null
  return MONTH.test(value) || value === 'undated' ? value : null
}

/** 标签只去首尾空白，取值范围由归档决定。 */
export function parseTag(value: string | null | undefined): string | null {
  return value?.trim() || null
}

const SANITIZERS: Readonly<Record<string, (value: string) => string | null>> = {
  platform: parsePlatform,
  month: parseMonth,
  tag: parseTag,
}

export function bucketOf(status: DisplayStatus): QueueBucket {
  for (const bucket of QUEUE_BUCKETS) {
    if (QUEUE_BUCKET_STATUSES[bucket].includes(status)) return bucket
  }
  // 未知状态归入未就绪，避免误报可审。
  return 'not_ready'
}

export const REVIEW_CONTEXT_KEYS = ['queue', 'platform', 'month', 'tag', 'alerts'] as const
export const HISTORY_CONTEXT_KEYS = ['platform', 'month', 'tag', 'page', 'limit'] as const
export const DETAIL_OWN_KEYS = ['tab'] as const

export type ReviewContextKey = (typeof REVIEW_CONTEXT_KEYS)[number]
export type HistoryContextKey = (typeof HISTORY_CONTEXT_KEYS)[number]

export type ListSource = 'review' | 'history'

export const CONTEXT_KEYS: Readonly<Record<ListSource, readonly string[]>> = {
  review: REVIEW_CONTEXT_KEYS,
  history: HISTORY_CONTEXT_KEYS,
}

type ParamsLike = URLSearchParams | Readonly<Record<string, string | null | undefined>>

function read(params: ParamsLike, key: string): string | null {
  if (params instanceof URLSearchParams) return params.get(key)
  return params[key] ?? null
}

/** 丢弃空值和非法筛选，避免空 platform 触发接口 422。 */
export function pickContext(params: ParamsLike, source: ListSource): URLSearchParams {
  const out = new URLSearchParams()
  for (const key of CONTEXT_KEYS[source]) {
    const raw = read(params, key)
    if (raw === null || raw === '') continue
    const sanitize = SANITIZERS[key]
    const value = sanitize ? sanitize(raw) : raw
    if (value === null || value === '') continue
    out.set(key, value)
  }
  return out
}

export function buildDetailSearch(
  params: ParamsLike,
  source: ListSource,
  own?: Readonly<{ tab?: string }>,
): string {
  const out = pickContext(params, source)
  if (own?.tab) out.set('tab', own.tab)
  return out.toString()
}

export function buildListSearch(params: ParamsLike, source: ListSource): string {
  return pickContext(params, source).toString()
}

/** 列表与详情必须共用查询参数，保证当前位置和相邻项一致。 */
export interface ReviewListQuery {
  readonly queue: QueueBucket
  readonly platform: Platform | null
  readonly month: string | null
  readonly tag: string | null
  readonly alerts: boolean
}

export function parseReviewListQuery(params: ParamsLike): ReviewListQuery {
  return {
    queue: parseQueue(read(params, 'queue')),
    platform: parsePlatform(read(params, 'platform')),
    month: parseMonth(read(params, 'month')),
    tag: parseTag(read(params, 'tag')),
    alerts: read(params, 'alerts') === '1',
  }
}

export interface HistoryListQuery {
  readonly platform: Platform | null
  readonly month: string | null
  readonly tag: string | null
  readonly page: number
  readonly limit: number
}

export function parseHistoryListQuery(params: ParamsLike): HistoryListQuery {
  const rawPage = Number(read(params, 'page'))
  const rawLimit = Number(read(params, 'limit'))
  const allowed: readonly number[] = HISTORY_PAGE_SIZES
  return {
    platform: parsePlatform(read(params, 'platform')),
    month: parseMonth(read(params, 'month')),
    tag: parseTag(read(params, 'tag')),
    page: Number.isInteger(rawPage) && rawPage >= 1 ? rawPage : 1,
    limit: allowed.includes(rawLimit) ? rawLimit : DEFAULT_HISTORY_LIMIT,
  }
}


/** 兼容 ?task=<account>/<post_id> 与 ?view=<name>，返回对应路由。 */
export function legacyRedirect(search: string): string | null {
  const params = new URLSearchParams(search)
  const task = params.get('task')
  const view = params.get('view')
  if (task === null && view === null) return null

  const base = view === 'history' ? '/history' : viewToPath(view)
  if (task) {
    const slash = task.indexOf('/')
    if (slash > 0 && slash < task.length - 1) {
      const account = task.slice(0, slash)
      const postId = task.slice(slash + 1)
      const root = view === 'history' ? '/history' : '/review'
      return `${root}/${encodeURIComponent(account)}/${encodeURIComponent(postId)}`
    }
    return base
  }
  return base
}

function viewToPath(view: string | null): string {
  switch (view) {
    case 'calendar':
      return '/calendar'
    case 'runtime':
      return '/runtime'
    case 'history':
      return '/history'
    default:
      return '/review'
  }
}
