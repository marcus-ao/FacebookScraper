import { Link, useParams, useSearchParams } from 'react-router'
import { Typography } from 'antd'

import { PageTitle } from '@/app/PageTitle'
import {
  buildListSearch,
  parseHistoryListQuery,
  parseReviewListQuery,
} from '@/app/search-params'
import { taskId } from '@/types/brands'

/**
 * ⚠️ **占位页。** 每个界面的正式实现各有归属的 Stage
 * （C 队列、D 详情、E′ 历史、F 月历、G 设置、H 运行状态）。
 *
 * 它只干一件事：把当前路由解析出来的 URL 契约**打印出来**，
 * 这样 DECISION_LOG §2.1–2.2 那两条修正是可见、可点、可验的，
 * 而不是等到 Stage C 才知道对不对。
 *
 * 页面标题由 `<PageTitle />` 画（文字来自路由 handle，和顶栏同源）。
 * Stage C/D 在那一行右侧接筛选与计数，不要再另起一个标题。
 */

const { Paragraph, Text } = Typography

function StageNote({ stage }: { stage: string }) {
  return (
    <Paragraph type="secondary" style={{ fontSize: 'var(--rc-font-secondary)' }}>
      占位页 · 正式实现属于 {stage}
    </Paragraph>
  )
}

function Dump({ value }: { value: unknown }) {
  return (
    <pre
      style={{
        background: 'var(--rc-surface-sunken)',
        border: '1px solid var(--rc-border)',
        borderRadius: 'var(--rc-radius)',
        padding: 'var(--rc-space-3)',
        fontSize: 'var(--rc-font-meta)',
        fontFamily: 'var(--rc-font-mono)',
        overflowX: 'auto',
      }}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export function Placeholder({ stage }: { stage: string }) {
  const [search] = useSearchParams()
  return (
    <section>
      <PageTitle />
      <StageNote stage={stage} />
      <Dump value={Object.fromEntries(search)} />
    </section>
  )
}

/** 审校队列占位：额外显示解析后的查询，和一个带上下文进详情的链接。 */
export function ReviewQueuePlaceholder() {
  const [search] = useSearchParams()
  return (
    <section>
      <PageTitle />
      <StageNote stage="Stage C" />
      <Dump value={parseReviewListQuery(search)} />
      <Paragraph>
        <Link to={{ pathname: '/review/fa_example/123', search: search.toString() }}>
          带着当前筛选进一条详情 →
        </Link>
      </Paragraph>
    </section>
  )
}

/** 历史占位：同上，另外显示 page / limit 的解析结果。 */
export function HistoryPlaceholder() {
  const [search] = useSearchParams()
  return (
    <section>
      <PageTitle />
      <StageNote stage="Stage E′" />
      <Dump value={parseHistoryListQuery(search)} />
      <Paragraph>
        <Link to={{ pathname: '/history/in_example.tech/456', search: search.toString() }}>
          带着当前筛选进一条归档详情 →
        </Link>
      </Paragraph>
    </section>
  )
}

/**
 * 详情占位：证明两件事——task id 能从两段路由参数拼回来，
 * 以及"返回列表"能按 DECISION_LOG §2.2 恢复原筛选。
 */
export function TaskDetailPlaceholder({ source }: { source: 'review' | 'history' }) {
  const params = useParams<{ account: string; postId: string }>()
  const [search] = useSearchParams()
  const id = params.account && params.postId ? taskId(params.account, params.postId) : null
  const backTo = {
    pathname: source === 'review' ? '/review' : '/history',
    search: buildListSearch(search, source),
  }
  return (
    <section>
      <PageTitle />
      <Paragraph type="secondary" style={{ fontSize: 'var(--rc-font-secondary)' }}>
        占位页 · 正式实现属于 Stage D · 来源列表：
        <Text strong>{source === 'review' ? '审校队列' : '历史归档'}</Text>
      </Paragraph>
      <Dump value={{ taskId: id, context: Object.fromEntries(search) }} />
      <Paragraph>
        <Link to={backTo}>← 返回列表（带回原筛选与页码）</Link>
      </Paragraph>
    </section>
  )
}
