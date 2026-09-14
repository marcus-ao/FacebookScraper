import { Button } from 'antd'

import { cx } from '@/lib/css'
import { DisabledReason } from './DisabledReason'
import styles from './PaidActionButton.module.css'

/**
 * 要花钱的动作：初翻、文案优化、图片优化。
 *
 * 两条口径（DESIGN.md §8.3）：
 *
 *   1. **不是 primary。** 她来这个页面是为了审一篇帖子，不是为了花钱生成东西。
 *      主按钮的位置留给「通过并创建排期」。付费动作用 `default` 形态。
 *
 *   2. **金额写在按钮上。** 旧 UI 的「生成图片 · 约 US$0.045」是对的，保留。
 *      按下去要扣真钱的按钮，价格不该藏在别处。
 *
 * ⛔ **disabled 必须给原因。**
 *
 * 旧 UI 最严重的一处：`ApprovalPanel.disabled` 是六个条件的或
 * （`editing || loading || submitting || !eligible || !options?.available || !when`），
 * 界面上只有一个灰按钮，她无从知道要先做什么（UI_AUDIT P2-10）。
 *
 * 这里用**类型**堵住：`disabledReason` 是唯一能让按钮变灰的入口。
 * 传了它就灰、就有 Tooltip；不传就是可点的。**构造不出"灰着但没有理由"的实例。**
 */

export interface PaidActionButtonProps {
  /** 动作名，例如「初翻」「文案优化」。 */
  readonly label: string
  /** 金额，例如 `US$0.045`。由调用方格式化 —— 这里不做货币运算。 */
  readonly amount: string
  /**
   * 不能点的原因。**给了它就 disabled，没给就可点。**
   * 这是让按钮变灰的唯一途径，所以不存在"灰着但说不出为什么"的状态。
   */
  readonly disabledReason?: string
  /** 剩余次数。不传就不显示 —— 没有数据时不画一个空的位置。 */
  readonly remaining?: number
  readonly loading?: boolean
  readonly onClick?: () => void
}

export function PaidActionButton({
  label,
  amount,
  disabledReason,
  remaining,
  loading = false,
  onClick,
}: PaidActionButtonProps) {
  const disabled = disabledReason !== undefined
  const text = `${label} · ${amount}`

  const button = (
    <Button
      // 付费动作不是 primary。
      className={cx(styles.button)}
      disabled={disabled}
      loading={loading}
      data-paid-action={label}
      {...(onClick ? { onClick } : {})}
    >
      {text}
    </Button>
  )

  return (
    <span className={cx(styles.wrap)}>
      {/* 灰着时由 DisabledReason 包一层：鼠标拿到 Tooltip 与 title，
          键盘 Tab 停在那一层上读到「动作名。当前不能操作：原因」。 */}
      <DisabledReason label={text} reason={disabledReason ?? ''}>
        {button}
      </DisabledReason>
      {remaining === undefined ? null : (
        <span className={cx(styles.remaining)}>剩余 {remaining} 次</span>
      )}
    </span>
  )
}
