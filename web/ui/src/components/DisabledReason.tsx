import type { ReactNode } from 'react'
import { Tooltip } from 'antd'

import { cx } from '@/lib/css'
import styles from './DisabledReason.module.css'

/**
 * 灰按钮的「为什么现在不能点」，对只用键盘的人也要拿得到。
 *
 * 原生 `disabled` 的 button 拿不到焦点。于是 Tooltip 的 hover 内容、按钮上的
 * `title`，键盘用户一个都走不到 —— 她 Tab 过去时那个按钮直接被跳过，屏幕上
 * 只剩一个灰块，不知道要先做什么。禁用原因必须同时提供给鼠标和键盘用户。
 *
 * 这里**不动按钮本身的启用判据**：该灰的还是灰。变的是外面那层包装 ——
 * 有原因时它自己成为一个可聚焦的注记，Tab 停在这里读到的是
 * 「通过并创建排期。当前不能操作：请先填写柏林发布时间」。
 * 没有原因时它整个消失，不留多余的 tab stop。
 *
 * ⛔ 不要为了无障碍把 disabled 换成「能点但拦 click」。那会让读屏把按钮念成
 *    可用的，按下去却什么都不发生 —— 比灰着更糟。
 */
export function DisabledReason({ label, reason, className, children }: {
  /** 动作名，读屏先念它，再念原因。 */
  readonly label: string
  /** 不能点的原因。**空串就是可点**，此时这一层不渲染。 */
  readonly reason: string
  readonly className?: string
  readonly children: ReactNode
}) {
  if (!reason) return <>{children}</>
  return (
    // trigger 带上 focus：键盘停在这里时，提示也要看得见，不能只有读屏念得到。
    <Tooltip title={reason} trigger={['hover', 'focus']}>
      {/* role="note" 是为了让 aria-label 生效 —— 裸 span 是 generic 角色，
          读屏可以合法地忽略它身上的名字。 */}
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
