import { Tag } from 'antd'

import { cx } from '@/lib/css'
import { STATUS_LABEL } from '@/lib/format'
import type { DisplayStatus } from '@/types/domain'
import styles from './StatusTag.module.css'

/**
 * 八个展示态的统一标记（七态 + `not_ready`）。
 *
 * 三条纪律（DESIGN.md），全部有测试守着：
 *
 *   1. **`scheduled` 的文案不许写成「已发布」。** 它只表示排期已被回读确认，
 *      不表示内容已经公开（web/DESIGN.md）。这是业务口径，不是措辞偏好。
 *
 *   2. **`skipped` 不用红色。** 「这篇不发」是她有意做的正常业务决定；
 *      用红色会让处理完的帖子看起来像出了故障，而红色在这个界面里
 *      只剩「确定错了」一个意思（DESIGN.md）。
 *
 *   3. **状态绝不只靠颜色。** 每个标记都带文字。理由有三个：色觉障碍；
 *      红黄在 12px 的小面积上区分度有限；旧 UI 的实证 —— `.row.done` 与
 *      `.row.waiting` 用同一个灰底，于是「处理完了」和「还没准备好」
 *      在视觉上完全不可区分。
 *
 * 文案来自 `lib/format.ts` 的 `STATUS_LABEL`，不在这里重抄一份 ——
 * 那是从旧 `format.js` 1:1 移植过来的表，只有 `not_ready`
 * 按 DECISION_LOG 改成了「未就绪」。
 */

/** 三档视觉重量。**只有三档**，不给八个状态各配一种颜色。 */
export type StatusTone = 'todo' | 'neutral' | 'done'

/**
 * 状态 → 视觉档位。
 *
 * `todo` = 她的待办，主色浅底，要能一眼扫出来。
 * `done` = 已经推进到系统侧的，成功浅底。
 * `neutral` = 其余全部 —— 包括两个终态。
 */
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

/**
 * 终态：她已经做完决定、不会再回来的两个。
 *
 * 列表行用它整行降饱和，把终态和 `not_ready` 区分开（DESIGN.md）——
 * 区分做在**行**上，不做在标记上，所以这两个状态的 `Tag` 仍然是中性的。
 *
 * ⚠️ `approved` 不在里面：它是「正在提交」，还在进行中。
 */
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
      // antd 6 起 `bordered={false}` 已废弃，正式写法是 variant="filled"。
      variant="filled"
      className={cx(styles.tag, TONE_CLASS[tone])}
      data-status={status}
      data-tone={tone}
    >
      {STATUS_LABEL[status]}
    </Tag>
  )
}
