import { cx } from '@/lib/css'
import { formatSchedule, formatTrailTime } from '@/lib/format'
import styles from './Time.module.css'


interface TimeProps {
  readonly at: string | null | undefined
  readonly fallback?: string
}

/** 带偏移的排期按同一业务时钟显示；无偏移的输入保持原墙上时刻。 */
export function BusinessTime({ at, fallback = '—' }: TimeProps) {
  const text = formatSchedule(at)
  if (text === null) {
    return <span className={cx(styles.empty)}>{fallback}</span>
  }
  return (
    <time className={cx(styles.time)} dateTime={at ?? undefined} data-zone="business">
      {text}
    </time>
  )
}

export function ShanghaiTime({ at, fallback = '—' }: TimeProps) {
  const text = formatTrailTime(at)
  if (text === '') {
    return <span className={cx(styles.empty)}>{fallback}</span>
  }
  return (
    <time className={cx(styles.time)} dateTime={at ?? undefined} data-zone="shanghai">
      {text}
    </time>
  )
}
