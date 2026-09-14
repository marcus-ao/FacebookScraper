import { Badge } from 'antd'
import { Link } from 'react-router'
import { useRuntime } from '@/hooks/useRuntime'
import { runtimeSummary } from '@/features/runtime/model'

import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/**
 * 顶栏右侧的运行状态入口。
 *
 * 「运行状态」是低频诊断页，入口位于顶栏右侧；`/runtime` 路由继续保留。
 *
 * 徽标由完整的 runtime 快照保守汇总。数据缺失、加载失败或阶段状态无法识别时
 * 保持中性，绝不把未知状态伪装成“运行正常”。
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
