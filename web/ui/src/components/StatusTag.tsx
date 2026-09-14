import { Tag } from 'antd'

import { cx } from '@/lib/css'
import { STATUS_LABEL } from '@/lib/format'
import type { DisplayStatus } from '@/types/domain'
import styles from './StatusTag.module.css'

/** 七个审校状态与 not_ready 的展示标记，文案来自 STATUS_LABEL。 */

export type StatusTone = 'todo' | 'neutral' | 'done'

export const STATUS_TONE: Readonly<Record<DisplayStatus, StatusTone>> = {
  not_ready: 'neutral',
  pending_review: 'todo',
  edited: 'todo',
  snoozed: 'neutral',
  approved: 'done',
  scheduled: 'done',
  skipped: 'neutral',
  handed_off: 'neutral',
}

/** 终态整行降饱和；approved 仍在提交中。 */
const TERMINAL: ReadonlySet<DisplayStatus> = new Set<DisplayStatus>(['skipped', 'handed_off'])

export function isTerminalStatus(status: DisplayStatus): boolean {
  return TERMINAL.has(status)
}

const TONE_CLASS: Readonly<Record<StatusTone, string | undefined>> = {
  todo: styles.todo,
  neutral: styles.neutral,
  done: styles.done,
}

export interface StatusTagProps {
  readonly status: DisplayStatus
}

export function StatusTag({ status }: StatusTagProps) {
  const tone = STATUS_TONE[status]
  return (
    <Tag
      variant="filled"
      className={cx(styles.tag, TONE_CLASS[tone])}
      data-status={status}
      data-tone={tone}
    >
      {STATUS_LABEL[status]}
    </Tag>
  )
}
