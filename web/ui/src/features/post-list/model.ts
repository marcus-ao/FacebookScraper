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
  const byStatus = { ...data.summary.by_status }
  if (previous.status !== detail.status) {
    byStatus[previous.status] = Math.max(0, (byStatus[previous.status] ?? 0) - 1)
    byStatus[detail.status] = (byStatus[detail.status] ?? 0) + 1
  }
  return {
    ...data,
    tasks: data.tasks.map(row => row.id !== detail.id ? row : {
      ...row, status: detail.status, review: detail.review, tags: detail.tags,
      schedule: detail.schedule, source_text_sha256: detail.text.source_text_sha256,
      text_de_excerpt: text.length > 90 ? text.slice(0, 90) + ' …' : text,
    }),
    summary: { ...data.summary, by_status: byStatus,
      tags: [...new Set(data.tasks.flatMap(row => row.id === detail.id ? detail.tags : row.tags))].sort(),
    },
  }
}

export function monthGroups(months: readonly string[]) {
  const years = new Map<string, string[]>()
  for (const month of [...new Set(months)].filter(Boolean).sort().reverse()) {
    const year = month.slice(0, 4)
    years.set(year, [...(years.get(year) ?? []), month])
  }
  return [...years].map(([year, values]) => ({ label: `${year} 年`,
    options: values.map(value => ({ value, label: value })),
  }))
}

export function isRowNavigationTarget(target: EventTarget | null): boolean {
  return target instanceof Element && !target.closest('a,button,input,select,textarea,[role="menuitem"],[role="checkbox"],[role="combobox"],[data-row-control]')
}
