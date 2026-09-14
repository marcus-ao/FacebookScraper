/**
 * URL 参数契约。**纯函数，没有 React 依赖**，所以能单测。
 *
 * 这个文件实现 DECISION_LOG.md §2.1–2.2 的两条架构修正：
 *
 *   1. 队列页签的参数叫 `queue`，不叫 `status` —— 一个分桶含多个真实 status，
 *      复用 `status` 会和"真实状态"撞车，也会让人以为能直接透传给后端。
 *
 *   2. 详情 URL 必须携带来源列表的全套筛选 —— 这样刷新详情页仍知道
 *      「第 n / N 篇」、上一篇/下一篇按进入时的筛选走、返回列表恢复原筛选与页码。
 *      `location.state` 只能当加速手段，不能当唯一真相源（它刷新后是 null，
 *      也不能分享）。
 */

import type { DisplayStatus, QueueBucket } from '@/types/domain'

export const QUEUE_BUCKETS = ['review', 'not_ready', 'snoozed', 'processed'] as const

/** 四个分桶各自包含哪些真实 status（DECISION_LOG.md D1）。 */
export const QUEUE_BUCKET_STATUSES: Readonly<Record<QueueBucket, readonly DisplayStatus[]>> = {
  review: ['pending_review', 'edited'],
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

/** 历史分页：默认 50，可选 20 / 50 / 100（DECISION_LOG.md D2）。 */
export const HISTORY_PAGE_SIZES = [20, 50, 100] as const
export const DEFAULT_HISTORY_LIMIT = 50

const isQueueBucket = (value: string | null): value is QueueBucket =>
  value !== null && (QUEUE_BUCKETS as readonly string[]).includes(value)

/** 未知或缺省一律回落 `review`。 */
export function parseQueue(value: string | null | undefined): QueueBucket {
  return isQueueBucket(value ?? null) ? (value as QueueBucket) : DEFAULT_QUEUE
}

/** 某个真实 status 属于哪个分桶。 */
export function bucketOf(status: DisplayStatus): QueueBucket {
  for (const bucket of QUEUE_BUCKETS) {
    if (QUEUE_BUCKET_STATUSES[bucket].includes(status)) return bucket
  }
  // 联合类型已经穷尽，走到这里说明后端加了新状态；归到未就绪最不容易误导。
  return 'not_ready'
}

/**
 * 进详情时要带走的参数。
 *
 * ⚠️ **只带这些**。多带会让 URL 变脏，少带会让「第 n / N 篇」在刷新后失效。
 */
export const REVIEW_CONTEXT_KEYS = ['queue', 'platform', 'month', 'tag', 'alerts'] as const
export const HISTORY_CONTEXT_KEYS = ['platform', 'month', 'tag', 'page', 'limit'] as const
/** 详情自己的参数。 */
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

/**
 * 从当前列表的参数里挑出要带进详情的那几个。
 *
 * 空串与 null 一律丢掉 —— 带上 `platform=` 这种空参数会让后端的 pattern 校验 422。
 */
export function pickContext(params: ParamsLike, source: ListSource): URLSearchParams {
  const out = new URLSearchParams()
  for (const key of CONTEXT_KEYS[source]) {
    const value = read(params, key)
    if (value === null || value === '') continue
    out.set(key, value)
  }
  return out
}

/** 详情 URL 的 search 串：来源上下文 + 详情自己的 tab。 */
export function buildDetailSearch(
  params: ParamsLike,
  source: ListSource,
  own?: Readonly<{ tab?: string }>,
): string {
  const out = pickContext(params, source)
  if (own?.tab) out.set('tab', own.tab)
  return out.toString()
}

/**
 * 返回列表时的 search 串：把 tab 这类详情自有参数摘掉，其余原样带回。
 *
 * 这是「返回列表恢复原来的筛选和页码」的实现（DECISION_LOG.md §2.2 第 3 条）。
 */
export function buildListSearch(params: ParamsLike, source: ListSource): string {
  return pickContext(params, source).toString()
}

/**
 * 列表查询的参数。详情页用**同一组**参数发起同一个列表查询，
 * 从结果里算当前 index 与相邻项——所以这个函数必须两边共用，不能各写一份。
 */
export interface ReviewListQuery {
  readonly queue: QueueBucket
  readonly platform: string | null
  readonly month: string | null
  readonly tag: string | null
  readonly alerts: boolean
}

export function parseReviewListQuery(params: ParamsLike): ReviewListQuery {
  return {
    queue: parseQueue(read(params, 'queue')),
    platform: read(params, 'platform') || null,
    month: read(params, 'month') || null,
    tag: read(params, 'tag') || null,
    alerts: read(params, 'alerts') === '1',
  }
}

export interface HistoryListQuery {
  readonly platform: string | null
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
    platform: read(params, 'platform') || null,
    month: read(params, 'month') || null,
    tag: read(params, 'tag') || null,
    page: Number.isInteger(rawPage) && rawPage >= 1 ? rawPage : 1,
    limit: allowed.includes(rawLimit) ? rawLimit : DEFAULT_HISTORY_LIMIT,
  }
}

// ─── 旧 URL 重定向（DECISION_LOG.md D11：永久保留，不设期限） ────────────────

/**
 * 旧应用的 URL 形状：`/?task=<account>/<post_id>` 与 `/?view=<name>`。
 *
 * ⚠️ 不能废：web/DESIGN.md §11 规定飞书卡片里带审校链接，外部系统里可能
 * 已经散落着这种 URL。
 *
 * 返回新的 `pathname + search`；不是旧形状时返回 null。
 */
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
    // task 不是 `<account>/<post_id>` 的形状：退回列表，不造一个坏详情 URL。
    return base
  }
  return base
}

function viewToPath(view: string | null): string {
  switch (view) {
    case 'calendar':
      return '/calendar'
    case 'settings':
      return '/settings'
    case 'runtime':
      return '/runtime'
    case 'history':
      return '/history'
    default:
      // 旧应用对未知 view 也是回落到任务列表（App.vue:20 的白名单）。
      return '/review'
  }
}
