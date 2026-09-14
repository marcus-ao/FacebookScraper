import { Tooltip } from 'antd'
import { ExclamationCircleFilled, UserOutlined, WarningFilled } from '@ant-design/icons'

import { cx } from '@/lib/css'
import type { HardAlert } from '@/types/domain'
import styles from './ProblemIndicator.module.css'

/**
 * 「这一篇有没有事」—— 与「处理到哪一步了」分开的那一列（DESIGN.md）。
 *
 * 状态回答进度，问题指示回答风险。两者是不同的列，不要混。
 *
 * **列表里的红色只出现在这一列。** 位置固定，她扫一列就知道哪几行要停下来。
 * 红色保留给需要停下来处理的问题，不能被普通动作稀释成背景噪声。
 *
 * ⛔ 完整告警文案**不常驻**，进 `Tooltip`（渐进披露 L2）。
 * 一行里塞一段散文，24 行就是 24 段，谁也不会读。
 */

export type ProblemKind = 'hard_alert' | 'risk' | 'third_party' | 'none'

/**
 * 判据来源。**只有列表载荷里真实存在的三个字段**
 * （`ReviewListItem.hard_alerts` / `risk_count` / `author_flag`）。
 *
 * ⛔ 不接详情才有的字段。列表和详情是两份不同的载荷，
 * 把详情字段当列表字段用是 DECISION_LOG 明令禁止的三条之一。
 */
export interface ProblemSource {
  readonly hard_alerts: readonly HardAlert[]
  readonly risk_count: number
  /** 常态 null；只有第三方作者才给值（reader.py `_author_kind`）。 */
  readonly author_flag: string | null
}

/**
 * 固定优先级：**硬闸 > 风险 > 第三方作者 > 无**。
 *
 * 一行只画一个图标。顺序是按"她必须先处理哪个"排的：
 * 硬闸是发不出去，风险是可能发错，第三方是需要先授权。
 */
export function problemOf(source: ProblemSource): ProblemKind {
  if (source.hard_alerts.length > 0) return 'hard_alert'
  if (source.risk_count > 0) return 'risk'
  if (source.author_flag !== null && source.author_flag !== '') return 'third_party'
  return 'none'
}

/** Tooltip 里那句话。业务语言，不出现字段名。 */
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
    // 没事就什么都不画。"一切正常"不需要一个图标来宣告（DESIGN.md）。
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
