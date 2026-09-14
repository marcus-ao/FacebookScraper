import { FacebookFilled, InstagramFilled } from '@ant-design/icons'

import { cx } from '@/lib/css'
import { PLATFORM_LABEL } from '@/lib/format'
import type { Platform } from '@/types/domain'
import styles from './PlatformLabel.module.css'

/**
 * 平台标识。**图标 + 文字，全应用一种写法。**
 *
 * 存在的理由不是好看，是旧 UI 里同一个东西有三种写法（`FB` / `Facebook` /
 * `facebook`），列表、详情、月历各写一套。
 *
 * ⛔ **不用品牌色。** 内容区里只有两种饱和色 —— 红（错了）和黄（注意看）
 * （DESIGN.md §5.1）。在列表的 72px 平台列里放一个品牌蓝和一个品牌粉，
 * 等于凭空多出两种饱和色，会削掉红黄两种真正的信号。
 * 所以图标走 `currentColor`，跟着周围的文字色走。
 *
 * `iconOnly` 留给窄屏。B2 不做复杂的 responsive API —— 谁需要谁传，
 * 真实的断点策略等 Stage C 在真实列表上量过再定（DESIGN.md §15）。
 */

const ICONS: Readonly<Record<Platform, React.ReactNode>> = {
  facebook: <FacebookFilled />,
  instagram: <InstagramFilled />,
}

export interface PlatformLabelProps {
  readonly platform: Platform
  /** 只画图标。文字仍然进 `aria-label`，读屏与 Tooltip 都还在。 */
  readonly iconOnly?: boolean
}

export function PlatformLabel({ platform, iconOnly = false }: PlatformLabelProps) {
  const label = PLATFORM_LABEL[platform]
  return (
    <span
      className={cx(styles.label)}
      data-platform={platform}
      {...(iconOnly ? { 'aria-label': label, title: label } : {})}
    >
      <span className={cx(styles.icon)} aria-hidden="true">
        {ICONS[platform]}
      </span>
      {iconOnly ? null : <span className={cx(styles.text)}>{label}</span>}
    </span>
  )
}
