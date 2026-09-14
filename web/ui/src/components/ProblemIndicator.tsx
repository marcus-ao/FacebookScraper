import { Tooltip } from 'antd'
import { ExclamationCircleFilled, UserOutlined, WarningFilled } from '@ant-design/icons'

import { cx } from '@/lib/css'
import type { HardAlert } from '@/types/domain'
import styles from './ProblemIndicator.module.css'

/** 问题指示与审校状态分开显示，详情在 Tooltip。 */

export type ProblemKind = 'hard_alert' | 'risk' | 'third_party' | 'none'

export interface ProblemSource {
  readonly hard_alerts: readonly HardAlert[]
  readonly risk_count: number
  readonly author_flag: string | null
}

/** 单图标优先级：硬闸 > 风险 > 第三方作者。 */
export function problemOf(source: ProblemSource): ProblemKind {
  if (source.hard_alerts.length > 0) return 'hard_alert'
  if (source.risk_count > 0) return 'risk'
  if (source.author_flag !== null && source.author_flag !== '') return 'third_party'
  return 'none'
}

export function problemTooltip(source: ProblemSource): string | null {
  switch (problemOf(source)) {
    case 'hard_alert':
      return source.hard_alerts.map((alert) => alert.label).join('；')
    case 'risk':
      return `${source.risk_count} 处语义风险待确认`
    case 'third_party':
      return source.author_flag
    case 'none':
      return null
  }
}

const ICONS: Readonly<Record<Exclude<ProblemKind, 'none'>, React.ReactNode>> = {
  hard_alert: <ExclamationCircleFilled />,
  risk: <WarningFilled />,
  third_party: <UserOutlined />,
}

const KIND_CLASS: Readonly<Record<Exclude<ProblemKind, 'none'>, string | undefined>> = {
  hard_alert: styles.hardAlert,
  risk: styles.risk,
  third_party: styles.thirdParty,
}

export interface ProblemIndicatorProps {
  readonly source: ProblemSource
}

export function ProblemIndicator({ source }: ProblemIndicatorProps) {
  const kind = problemOf(source)
  if (kind === 'none') {
    return <span className={cx(styles.none)} data-problem="none" aria-hidden="true" />
  }

  const tip = problemTooltip(source) ?? ''
  return (
    <Tooltip title={tip}>
      <span
        className={cx(styles.icon, KIND_CLASS[kind])}
        data-problem={kind}
        role="img"
        aria-label={tip}
      >
        {ICONS[kind]}
      </span>
    </Tooltip>
  )
}
