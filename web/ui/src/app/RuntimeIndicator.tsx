import { Badge } from 'antd'
import { Link } from 'react-router'
import { useRuntime } from '@/hooks/useRuntime'
import { runtimeSummary } from '@/features/runtime/model'

import { cx } from '@/lib/css'
import styles from './AppShell.module.css'

/** 运行快照缺失或不可识别时保持未知，不推定正常。 */
export function RuntimeIndicator() {
  const query = useRuntime()
  const summary = runtimeSummary(query.error ? undefined : query.data)
  return (
    <Link to="/runtime" className={cx(styles.runtime)}>
      <Badge status={summary.tone} text={`运行状态 · ${summary.label}`} />
    </Link>
  )
}
