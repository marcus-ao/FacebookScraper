import { cx } from '@/lib/css'
import { formatSchedule, formatTrailTime } from '@/lib/format'
import styles from './Time.module.css'


interface TimeProps {
  readonly at: string | null | undefined
  readonly fallback?: string
}

/** 排期按字符串携带的业务墙上时刻显示，不转为宿主时区。 */
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
