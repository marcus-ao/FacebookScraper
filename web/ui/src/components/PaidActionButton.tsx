import { Button } from 'antd'

import { cx } from '@/lib/css'
import { DisabledReason } from './DisabledReason'
import styles from './PaidActionButton.module.css'

/** 使用模型额度的次要动作；禁用必须提供原因。 */

export interface PaidActionButtonProps {
  readonly label: string
  readonly disabledReason?: string
  readonly remaining?: number
  readonly loading?: boolean
  readonly onClick?: () => void
}

export function PaidActionButton({
  label,
  disabledReason,
  remaining,
  loading = false,
  onClick,
}: PaidActionButtonProps) {
  const disabled = disabledReason !== undefined
  const text = label

  const button = (
    <Button
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
      <DisabledReason label={text} reason={disabledReason ?? ''}>
        {button}
      </DisabledReason>
      {remaining === undefined ? null : (
        <span className={cx(styles.remaining)}>剩余 {remaining} 次</span>
      )}
    </span>
  )
}
