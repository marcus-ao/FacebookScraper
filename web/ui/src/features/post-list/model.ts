import { bucketOf, QUEUE_BUCKETS, QUEUE_BUCKET_STATUSES } from '@/app/search-params'
import type { ReviewListQuery } from '@/app/search-params'
import type { DisplayStatus, Platform, QueueBucket, ReviewListItem, ReviewListResponse, TaskDetail } from '@/types/domain'

export function filterReviewRows(rows: readonly ReviewListItem[], filters: ReviewListQuery) {
  return rows.filter(row => bucketOf(row.status) === filters.queue
    && (!filters.platform || row.platform === filters.platform)
    && (!filters.month || row.month === filters.month)
    && (!filters.tag || (filters.tag === '__untagged__' ? row.tags.length === 0 : row.tags.includes(filters.tag)))
    && (!filters.alerts || row.hard_alerts.length > 0))
}

/** platform 给定时只数那个平台：两个入口各自独立，角标不能互相串。 */
export function queueCounts(summary: ReviewListResponse['summary'],
                            platform?: Platform): Record<QueueBucket, number> {
  const byStatus: Partial<Record<DisplayStatus, number>> =
    (platform ? summary.by_platform_status?.[platform] : summary.by_status) ?? summary.by_status
  return Object.fromEntries(QUEUE_BUCKETS.map(bucket => [bucket,
    QUEUE_BUCKET_STATUSES[bucket].reduce((count, status) => count + (byStatus[status] ?? 0), 0),
  ])) as Record<QueueBucket, number>
}

export function patchReviewList(data: ReviewListResponse, detail: TaskDetail): ReviewListResponse {
  const previous = data.tasks.find(row => row.id === detail.id)
  if (!previous) return data
  const text = (detail.text.de_human || detail.text.de_machine || '').replace(/\s+/g, ' ').trim()
  const hardAlerts = detail.hard_alerts ?? previous.hard_alerts
  const alertChange = Number(hardAlerts.length > 0) - Number(previous.hard_alerts.length > 0)
  const platformAlerts = data.summary.by_platform_hard_alerts
    ? { ...data.summary.by_platform_hard_alerts,
        [detail.platform]: Math.max(0, data.summary.by_platform_hard_alerts[detail.platform] + alertChange) }
    : undefined
  const byStatus = { ...data.summary.by_status }
  // 角标读的是 by_platform_status，两份都得跟着动，否则审完一篇页签数字原地不动。
  const byPlatform = data.summary.by_platform_status
    ? { ...data.summary.by_platform_status,
        [detail.platform]: { ...data.summary.by_platform_status[detail.platform] } }
    : undefined
  if (previous.status !== detail.status) {
    byStatus[previous.status] = Math.max(0, (byStatus[previous.status] ?? 0) - 1)
    byStatus[detail.status] = (byStatus[detail.status] ?? 0) + 1
    const lane = byPlatform?.[detail.platform] as Record<string, number> | undefined
    if (lane) {
      lane[previous.status] = Math.max(0, (lane[previous.status] ?? 0) - 1)
      lane[detail.status] = (lane[detail.status] ?? 0) + 1
    }
  }
  return {
    ...data,
    tasks: data.tasks.map(row => row.id !== detail.id ? row : {
      ...row, status: detail.status, review: detail.review, tags: detail.tags,
      schedule: detail.schedule, source_text_sha256: detail.text.source_text_sha256,
      hard_alerts: hardAlerts,
      text_de_excerpt: text.length > 90 ? text.slice(0, 90) + ' …' : text,
    }),
    summary: { ...data.summary, by_status: byStatus,
      with_hard_alerts: Math.max(0, data.summary.with_hard_alerts + alertChange),
      ...(byPlatform ? { by_platform_status: byPlatform } : {}),
      ...(platformAlerts ? { by_platform_hard_alerts: platformAlerts } : {}),
      tags: [...new Set(data.tasks.flatMap(row => row.id === detail.id ? detail.tags : row.tags))].sort(),
    },
  }
}

export function monthGroups(months: readonly string[]) {
  const years = new Map<string, string[]>()
  // undated 不是年份分组：切片会算出「unda 年」这种破碎标签。
  for (const month of [...new Set(months)].filter(value => value && value !== 'undated').sort().reverse()) {
    const year = month.slice(0, 4)
    years.set(year, [...(years.get(year) ?? []), month])
  }
  const groups = [...years].map(([year, values]) => ({ label: `${year} 年`,
    options: values.map(value => ({ value, label: value })) }))
  if (months.includes('undated')) groups.push({ label: '其他', options: [{ value: 'undated', label: '无日期' }] })
  return groups
}

export function isRowNavigationTarget(target: EventTarget | null): boolean {
  return target instanceof Element && !target.closest('a,button,input,select,textarea,[role="menuitem"],[role="checkbox"],[role="combobox"],[data-row-control]')
}
