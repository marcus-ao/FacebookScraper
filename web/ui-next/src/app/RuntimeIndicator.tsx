import { Badge } from 'antd'
import { Link } from 'react-router'
import { useRuntime } from '@/hooks/useRuntime'
import { runtimeSummary } from '@/features/runtime/model'

import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/**
 * 顶栏右侧的运行状态入口。
 *
 * 「运行状态」从主导航降级到这里（DECISION_LOG.md D9）：它是一天用不到一次
 * 的诊断页，不该和四个业务界面平起平坐。`/runtime` 路由继续存在，入口只有这一个。
 *
 * ⚠️ **Stage B1 不显示任何"运行正常"。**
 *
 * 真实语义需要把 `GET /api/runtime` 的五个阶段映射成一个词，而那五个阶段的
 * 字段各不相同、后端也没有给判别字段（REACT_MIGRATION_PLAN.md §3.5 第 12 条）。
 * 在做出那个映射之前写一个绿点，等于对她撒谎 —— 而这个指示器存在的唯一理由
 * 就是让她能信它。所以这里是中性 `default` 徽标 + 一个词，点进去看真的页面。
 *
 * 真实状态语义归 Stage H。那时把 `status` 换成 success / warning / error 即可，
 * 这个组件的形状不用改。
 *
 * 用 `Link` 而不是 `Button`：它就是一个链接，要能中键新开、要能复制地址。
 * 把 `<a>` 塞进 `<button>` 里两样都做不到，而且是非法 HTML。
 */
export function RuntimeIndicator() {
  const query = useRuntime()
  const summary = runtimeSummary(query.error ? undefined : query.data)
  return (
    <Link to="/runtime" className={cx(styles.runtime)}>
      <Badge status={summary.tone} text={`运行状态 · ${summary.label}`} />
    </Link>
  )
}
