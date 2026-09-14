import { FacebookFilled, InstagramFilled } from '@ant-design/icons'

import { cx } from '@/lib/css'
import { PLATFORM_LABEL } from '@/lib/format'
import type { Platform } from '@/types/domain'
import styles from './PlatformLabel.module.css'

/** 平台图标与文字共用 currentColor。 */

const ICONS: Readonly<Record<Platform, React.ReactNode>> = {
  facebook: <FacebookFilled />,
  instagram: <InstagramFilled />,
}

export interface PlatformLabelProps {
  readonly platform: Platform
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
