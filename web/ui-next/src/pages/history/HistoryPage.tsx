import { useEffect } from 'react'
import { Alert, Button, Empty, Pagination, Tooltip } from 'antd'
import { LockOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router'
import { PageTitle } from '@/app/PageTitle'
import { buildDetailSearch, HISTORY_PAGE_SIZES, parseHistoryListQuery } from '@/app/search-params'
import { tokens } from '@/app/theme'
import { useHistoryList } from '@/hooks/useTasks'
import { createPostColumns } from '@/features/post-list/columns'
import { ListFilters } from '@/features/post-list/ListFilters'
import { PostTable } from '@/features/post-list/PostTable'
import type { HistoryListItem } from '@/types/domain'
import { idPath } from '@/services/http'
import styles from '@/features/post-list/PostTable.module.css'

export function HistoryPage() {
  const [search, setSearch] = useSearchParams()
  const filters = parseHistoryListQuery(search)
  const query = useHistoryList(filters)
  useEffect(() => {
    if (search.get('page') !== String(filters.page) || search.get('limit') !== String(filters.limit)) {
      const next = new URLSearchParams(search); next.set('page', String(filters.page)); next.set('limit', String(filters.limit))
      setSearch(next, { replace: true })
    }
  }, [search, filters.page, filters.limit, setSearch])
  const href = (row: HistoryListItem) => `/history/${idPath(row.id)}?${buildDetailSearch(search, 'history')}`
  const columns = createPostColumns<HistoryListItem>({ columns:['thumbnail','summary','status','platform','tags'],summaryHref:href })
  columns.unshift({ key:'date', title:'日期', width:tokens.layout.platformColumnWidth,
    render: (_:unknown,row:HistoryListItem) => <time dateTime={row.created_at}>{row.created_at?.slice(0,10) || '—'}</time> })
  columns.splice(5,0,{key:'account',title:'账号',width:tokens.layout.accountColumnWidth,
    render: (_:unknown,row:HistoryListItem) => <span className={styles.account} title={row.account}>
      {row.read_only && <Tooltip title="冻结账号，只供查阅"><LockOutlined aria-label="冻结账号" /></Tooltip>}{row.account}
    </span> })
  function change(key: 'platform'|'month'|'tag', value: string|undefined) {
    const next=new URLSearchParams(search)
    if(value) next.set(key,value); else next.delete(key)
    next.set('page','1'); setSearch(next)
  }
  return <section className={styles.page}>
    <div className={styles.head}>
      <div className={styles.heading}><PageTitle />
        <Pagination size="small" current={filters.page} pageSize={filters.limit} total={query.data?.pagination.total ?? 0}
          showSizeChanger pageSizeOptions={[...HISTORY_PAGE_SIZES]} showTotal={total=>`共 ${total} 篇`}
          onChange={(page,limit)=>{const next=new URLSearchParams(search);next.set('page',String(limit===filters.limit?page:1));next.set('limit',String(limit));setSearch(next)}} />
      </div>
      <div className={styles.toolbar}><ListFilters filters={filters} months={query.data?.summary.months ?? []}
        tags={query.data?.summary.tags ?? []} onChange={change} /></div>
    </div>
    {query.error && <Alert type="error" showIcon title="暂时无法读取历史归档" description={query.error.message}
      action={<Button onClick={()=>void query.refetch()}>重试</Button>} />}
    {query.data?.index.stale && <Alert type="warning" banner title="列表更新暂有延迟，当前已从归档重新读取。" />}
    <PostTable rows={[...(query.data?.tasks ?? [])]} columns={columns} loading={query.isFetching} href={href}
      empty={<Empty description="当前筛选下没有归档帖子。" />} />
  </section>
}
