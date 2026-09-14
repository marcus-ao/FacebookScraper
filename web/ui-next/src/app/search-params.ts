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

import type { DisplayStatus, Platform, QueueBucket } from '@/types/domain'

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

// ─── 手改 URL 的边界 ────────────────────────────────────────────────────────
//
// 这三个参数都会原样发给后端，所以形状必须和真实契约对齐，**逐个查过**：
//
//   platform  web/api/app.py:95 写死 pattern `^(facebook|instagram)$`。
//             历史页的筛选是服务端做的，`?platform=facebookk` 直接 422 ——
//             她看到的是整页「暂时无法读取历史归档」。
//   month     后端没有 pattern，只做等值匹配（core/index_db.py:182），非法值
//             不报错、永远查不到东西。取值来自 `created_at[:7]`，另外
//             core/index_db.py:113 会给缺日期的帖子写一个真实取值 `undated`，
//             它也在筛选下拉里，所以不能只认 YYYY-MM。
//   tag       后端没有 pattern，是自由业务标签（core/index_db.py:186 另外认一个
//             `__untagged__` 哨兵）。⛔ 不要在这里编一份白名单 —— 归档里有什么
//             标签是运营说了算的，前端拦一下就等于把真实数据筛没了。

const PLATFORMS: readonly Platform[] = ['facebook', 'instagram']
const MONTH = /^\d{4}-(?:0[1-9]|1[0-2])$/

/** 非法一律 null：宁可当成"没筛"，也不要把 422 甩到她脸上。 */
export function parsePlatform(value: string | null | undefined): Platform | null {
  return PLATFORMS.find(platform => platform === value) ?? null
}

export function parseMonth(value: string | null | undefined): string | null {
  if (!value) return null
  return MONTH.test(value) || value === 'undated' ? value : null
}

/** 只去首尾空白。自由标签的取值范围由归档决定，不由这里决定。 */
export function parseTag(value: string | null | undefined): string | null {
  return value?.trim() || null
}

const SANITIZERS: Readonly<Record<string, (value: string) => string | null>> = {
  platform: parsePlatform,
  month: parseMonth,
  tag: parseTag,
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
 * 非法值同样丢掉，所以手改坏的 URL 走一圈回来就被洗干净了，不会再传下去。
 */
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
