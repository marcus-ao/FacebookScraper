import type { ReactNode } from 'react'
import { Tooltip } from 'antd'

import { cx } from '@/lib/css'
import styles from './DisabledReason.module.css'

/** 禁用原因由可聚焦包装层提供，按钮保留真实 disabled。 */
export function DisabledReason({ label, reason, className, children }: {
  readonly label: string
  readonly reason: string
  readonly className?: string
  readonly children: ReactNode
}) {
  if (!reason) return <>{children}</>
  return (
    <Tooltip title={reason} trigger={['hover', 'focus']}>
      {/* note 角色使包装层的 aria-label 可被读取。 */}
      <span
        role="note"
        tabIndex={0}
        title={reason}
        aria-label={`${label}。当前不能操作：${reason}`}
        className={cx(styles.wrap, className)}
      >
        {children}
      </span>
    </Tooltip>
  )
}
