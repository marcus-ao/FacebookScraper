import { cx } from '@/lib/css'
import { formatSchedule, formatTrailTime } from '@/lib/format'
import styles from './Time.module.css'

/**
 * 两个时刻组件。**这是业务正确性组件，不是样式组件。**
 *
 * 它们存在的唯一理由：让 `new Date(...)` / `toLocaleString(...)` 在页面代码里
 * 一次都不出现。
 *
 * 这台机器在中国。一个"看起来正常"的 `toLocaleString` 会把 10:00 柏林显示成
 * 16:00 —— 而柏林排期时刻是这个项目最不能出错的一类数，夏令时切换日尤其。
 * 旧 UI 的 `format.js` 因此**手工解析字符串里带的偏移**，从不交给浏览器本地时区；
 * 这条纪律被 DECISION_LOG §5.3 列为"不许变"的九条之一。
 *
 * 所以：
 *
 *   - 两个组件都只调 `lib/format.ts`，自己不做任何时间运算；
 *   - `format.ts` 的 24 条单测跑在 `TZ=America/New_York` 下 ——
 *     故意不用柏林也不用上海，任何本地时区泄漏都会当场露出来；
 *   - 这个文件自己的测试也跑在同一个时区里，再验一遍组件这层没有偷偷加工。
 */

interface TimeProps {
  /** ISO 字符串。null / 空串一律走 `fallback`，**不显示空白**。 */
  readonly at: string | null | undefined
  /** 没有值时显示什么。默认破折号 —— 不确定的值显示为占位，不是空（DESIGN.md §1.2）。 */
  readonly fallback?: string
}

/**
 * 柏林时刻。**排期用它。**
 *
 * ⛔ `schedule.at` 绝不经过浏览器本地时区重新解释：按字符串里带的
 * `+02:00` / `+01:00` 偏移直接读墙上时刻。
 *
 * 输出形如 `9/13 周日 17:00 柏林`。时区词是格式的一部分，不是装饰 ——
 * 她看到的每一个时刻都必须自带"这是哪里的时间"。
 */
export function BerlinTime({ at, fallback = '—' }: TimeProps) {
  const text = formatSchedule(at)
  if (text === null) {
    return <span className={cx(styles.empty)}>{fallback}</span>
  }
  return (
    <time className={cx(styles.time)} dateTime={at ?? undefined} data-zone="berlin">
      {text}
    </time>
  )
}

export interface ShanghaiTimeProps extends TimeProps {
  /**
   * 是否在末尾加「上海」。
   *
   * 默认加 —— DESIGN.md §1.2 要求"时刻永远带时区词"，而这个界面上同时存在
   * 柏林与上海两套时刻，不标出来就会被当成同一套。
   *
   * 旧 UI 的 `formatTrailTime` 不带时区词、`formatWakeAt` 带。
   * 需要与旧 UI 逐字一致的地方传 `showZone={false}`。
   */
  readonly showZone?: boolean
}

/**
 * 上海时刻。**操作记录、`wake_at` 这类"她自己的时间"用它。**
 *
 * 走 `Intl.DateTimeFormat` 但**显式钉 `timeZone: 'Asia/Shanghai'`**，
 * 不靠机器的本地时区碰巧是对的。
 */
export function ShanghaiTime({ at, fallback = '—', showZone = true }: ShanghaiTimeProps) {
  const text = formatTrailTime(at)
  if (text === '') {
    return <span className={cx(styles.empty)}>{fallback}</span>
  }
  return (
    <time className={cx(styles.time)} dateTime={at ?? undefined} data-zone="shanghai">
      {showZone ? `${text} 上海` : text}
    </time>
  )
}
