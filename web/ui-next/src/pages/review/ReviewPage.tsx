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
import type { ReviewListItem } from '@/types/domain'
import { idPath } from '@/services/http'
import styles from '@/features/post-list/PostTable.module.css'

export function ReviewPage() {
  const query = useReviewList()
  const [search, setSearch] = useSearchParams()
  const filters = parseReviewListQuery(search)
  const location = useLocation()
  const navigationType = useNavigationType()
  useEffect(() => {
    // 只有明确点击导航才刷新。POP 返回同一个历史条目也不会重复执行该意图。
    if (navigationType === 'PUSH' && location.state?.refreshReview && query.data) void query.refetch()
  }, [location.key])
  const rows = filterReviewRows(query.data?.tasks ?? [], filters)
  const counts = query.data ? queueCounts(query.data.summary) : null
  const href = (row: ReviewListItem) => `/review/${idPath(row.id)}?${buildDetailSearch(search, 'review')}`
  const columns = createPostColumns<ReviewListItem>({
    columns: ['problem', 'thumbnail', 'summary', 'status', 'time', 'platform', 'tags', 'actions'],
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
        {(query.data?.summary.with_hard_alerts ?? 0) > 0 && <Tag closable={filters.alerts}
          onClose={event => { event.preventDefault(); change('alerts', undefined) }}
          onClick={() => change('alerts', filters.alerts ? undefined : '1')} role="button" tabIndex={0}
          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change('alerts', filters.alerts ? undefined : '1') } }}>
          {filters.alerts ? '只看硬闸' : '硬闸'} {query.data?.summary.with_hard_alerts}
        </Tag>}
        近 {query.data?.range.days ?? 90} 天 · {query.data?.summary.total ?? 0} 篇
      </span></div>
      <div className={styles.toolbar}>
        <Tabs activeKey={filters.queue} onChange={value => change('queue', value)} items={QUEUE_BUCKETS.map(key => ({
          key, label: `${QUEUE_BUCKET_LABEL[key]} ${counts?.[key] ?? 0}`,
        }))} />
        <ListFilters filters={filters} months={query.data?.tasks.map(row => row.month) ?? []}
          tags={query.data?.summary.tags ?? []} onChange={change} />
      </div>
    </div>
    {query.error && <div className={styles.error}><Alert type="error" showIcon title="暂时无法读取审校队列"
      description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} /></div>}
    {query.data?.index.stale && <Alert type="warning" banner title="列表更新暂有延迟，当前已从归档重新读取。" />}
    <PostTable rows={rows} columns={columns} loading={query.isFetching} href={href}
      empty={<Empty description="当前筛选下没有帖子。"><Button onClick={() => setSearch({ queue: 'review' })}>清除筛选</Button></Empty>} />
  </section>
}
