import { useEffect } from 'react'
import { Alert, Button, Empty, Tabs, Tag } from 'antd'
import { useLocation, useNavigationType, useSearchParams } from 'react-router'
import { PageTitle } from '@/app/PageTitle'
import { buildDetailSearch, parseReviewListQuery, QUEUE_BUCKET_LABEL, QUEUE_BUCKETS } from '@/app/search-params'
import { useReviewList } from '@/hooks/useTasks'
import { createPostColumns } from '@/features/post-list/columns'
import { filterReviewRows, queueCounts } from '@/features/post-list/model'
import { ListFilters } from '@/features/post-list/ListFilters'
import { PostTable } from '@/features/post-list/PostTable'
import { ReviewActions } from '@/features/review-actions/ReviewActions'
import type { Platform, ReviewListItem } from '@/types/domain'
import { idPath } from '@/services/http'
import styles from '@/features/post-list/PostTable.module.css'

export function ReviewPage({ platform }: { platform: Platform }) {
  const query = useReviewList()
  const [search, setSearch] = useSearchParams()
  // 平台来自路由，不是筛选项——这个入口只看这一个平台。
  const filters = { ...parseReviewListQuery(search), platform }
  const location = useLocation()
  const navigationType = useNavigationType()
  useEffect(() => {
    // 只有明确点击导航才刷新。POP 返回同一个历史条目也不会重复执行该意图。
    if (navigationType === 'PUSH' && location.state?.refreshReview && query.data) void query.refetch()
  }, [location.key])
  const rows = filterReviewRows(query.data?.tasks ?? [], filters)
  const counts = query.data ? queueCounts(query.data.summary, platform) : null
  const platformRows = (query.data?.tasks ?? []).filter(row => row.platform === platform)
  const total = counts ? Object.values(counts).reduce((sum, count) => sum + count, 0) : 0
  const hardAlerts = query.data?.summary.by_platform_hard_alerts?.[platform]
    ?? platformRows.filter(row => row.hard_alerts.length > 0).length
  const platformLabel = platform === 'facebook' ? 'Facebook' : 'Instagram'
  // 详情要带上平台，返回时才知道回哪个入口。
  const detailSearch = () => {
    const params = new URLSearchParams(search)
    params.set('platform', platform)
    return buildDetailSearch(params, 'review')
  }
  const href = (row: ReviewListItem) => `/review/${idPath(row.id)}?${detailSearch()}`
  const columns = createPostColumns<ReviewListItem>({
    // 不带 platform 列：这个入口里每一行都是同一个平台，显示出来是噪声。
    columns: ['problem', 'thumbnail', 'summary', 'status', 'time', 'tags', 'actions'],
    summaryHref: href,
    summaryNote: row => row.hard_alerts.some(alert => alert.code === 'unknown_collaborator')
      ? <span className={styles.thirdParty}>第三方作者 · 需授权初翻</span> : null,
    problem: row => row,
    time: { title: '时刻 · 柏林', zone: 'berlin', at: row => row.schedule?.at ?? null },
    actions: { render: row => <ReviewActions detail={row} compact /> },
  })
  function change(key: string, value: string | undefined) {
    const next = new URLSearchParams(search)
    if (value) next.set(key, value); else next.delete(key)
    setSearch(next)
  }
  return <section className={styles.page}>
    <div className={styles.head}>
      <div className={styles.heading}><PageTitle /><span className={styles.meta}>
        {hardAlerts > 0 && <Tag closable={filters.alerts}
          onClose={event => { event.preventDefault(); change('alerts', undefined) }}
          onClick={() => change('alerts', filters.alerts ? undefined : '1')} role="button" tabIndex={0}
          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change('alerts', filters.alerts ? undefined : '1') } }}>
          {filters.alerts ? '只看硬闸' : '硬闸'} {hardAlerts}
        </Tag>}
        近 {query.data?.range.days ?? 90} 天 · {total} 篇
      </span></div>
      <div className={styles.toolbar}>
        <Tabs activeKey={filters.queue} onChange={value => change('queue', value)} items={QUEUE_BUCKETS.map(key => ({
          key, label: `${QUEUE_BUCKET_LABEL[key]} ${counts?.[key] ?? 0}`,
        }))} />
        {/* 平台不在这里筛：它是入口本身。历史页仍然保留跨平台检索。 */}
        <ListFilters filters={filters} months={platformRows.map(row => row.month)}
          tags={[...new Set(platformRows.flatMap(row => row.tags))].sort()} onChange={change} platformFilter={false} />
      </div>
    </div>
    {query.error && <div className={styles.error}><Alert type="error" showIcon title="暂时无法读取审校队列"
      description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} /></div>}
    {query.data?.index.stale && <Alert type="warning" banner title="列表更新暂有延迟，当前已从归档重新读取。" />}
    <PostTable rows={rows} columns={columns} loading={query.isFetching} href={href}
      empty={<Empty description={`${platformLabel} 当前筛选下没有帖子。`}><Button onClick={() => setSearch({ queue: 'review' })}>清除筛选</Button></Empty>} />
  </section>
}
