// 展示口径。**只管怎么显示，不含任何判断**——判断全在后端。
//
// ⛔ 从 web/ui/src/format.js **算法 1:1 移植**，只加类型（DECISION_LOG.md §5.3）。
//    唯一有意改动的是 STATUS_LABEL.not_ready：'待处理' → '未就绪'，
//    依据 DECISION_LOG.md D1 第 2 条与 §3.1。其余文案一字不改。

import type {
  AuthorKind,
  DisplayStatus,
  Platform,
  RiskKind,
  TrailAction,
} from '@/types/domain'

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

/** 仅把后端有偏移的时刻转换成柏林输入值；用户输入保持原字符串，由服务端判定 DST。 */
export function berlinInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(value).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

/** 比较两条柏林墙上时刻的缓存间隔，不负责判断 DST 合法性。 */
export const wallMinutesApart = (a: string, b: string) => Math.abs(Date.parse(a.slice(0, 16) + ':00Z') - Date.parse(b.slice(0, 16) + ':00Z')) / 60000

/** 月历只迭代日期标签；跨月边界和每张卡的柏林日期均来自后端。 */
export function calendarDays(start: string, end: string): (string | null)[] {
  const first = new Date(start.slice(0, 10) + 'T00:00:00Z'), last = new Date(end.slice(0, 10) + 'T00:00:00Z')
  if (!Number.isFinite(first.getTime()) || !Number.isFinite(last.getTime())) return []
  const days: (string | null)[] = Array.from({ length: (first.getUTCDay() + 6) % 7 }, () => null)
  for (let stamp = first.getTime(); stamp <= last.getTime(); stamp += 86400000) days.push(new Date(stamp).toISOString().slice(0, 10))
  return days
}

// 排期时刻带的是柏林偏移（+02:00 / +01:00）。**不能交给浏览器的本地时区去渲染**：
// 这台机器在中国，直接 toLocaleString 会把 10:00 柏林显示成 16:00，
// 而那正是这个项目最不能出错的一类数（夏令时切换日尤其）。
// 所以按字符串里带的偏移自己算，显示成柏林当地时刻。
export function formatSchedule(iso: string | null | undefined): string | null {
  if (!iso) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return null
  const [, y, mo, d, h, mi] = match
  // 正则匹配成功时这五组一定存在；noUncheckedIndexedAccess 下显式判一次。
  if (y === undefined || mo === undefined || d === undefined || h === undefined || mi === undefined) {
    return null
  }
  // getUTCDay() 恒在 0..6，但 noUncheckedIndexedAccess 下类型仍是可选；
  // 不给兜底的话模板字符串会把 undefined 原样印出来，那是静默错误。
  const weekday = WEEKDAYS[new Date(Date.UTC(+y, +mo - 1, +d)).getUTCDay()] ?? ''
  return `${+mo}/${+d} ${weekday} ${h}:${mi} 柏林`
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return String(iso).slice(0, 10)
}

export function formatTrailTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(value)
}

export function formatWakeAt(iso: string | null | undefined): string {
  return iso ? `${formatTrailTime(iso)} 上海` : ''
}

export const PLATFORM_LABEL: Record<Platform, string> = {
  facebook: 'Facebook',
  instagram: 'Instagram',
}

// 业务视角七态 + not_ready（web/DESIGN.md 第 4 节 / core/review.py:16）。
//
// ⚠️ not_ready 的文案是本次唯一的有意改动：旧 UI 显示「待处理」，新 UI 显示
// 「未就绪」。因为新队列有四个页签，「待我审」才是她的待办，把 not_ready
// 也叫「待处理」会让两个概念撞车（DECISION_LOG.md D1）。
export const STATUS_LABEL: Record<DisplayStatus, string> = {
  not_ready: '未就绪',
  pending_review: '待我审',
  edited: '已修改',
  snoozed: '已挂起',
  approved: '已通过',
  scheduled: '已排期',
  skipped: '这篇不发',
  handed_off: '已交人工处理',
}

export const AUTHOR_KIND_LABEL: Record<AuthorKind, string> = {
  own: '原创',
  collab: '合作帖',
  third_party: '第三方作者',
}

// 风险三类（web/DESIGN.md 第 8 节）。金额/单位/标签由 regex 负责，不进这里。
export const RISK_KIND_LABEL: Record<RiskKind, string> = {
  pun: '俚语双关',
  ambiguous: '歧义句',
  us_only: '美国限定',
}

// 操作记录的动作文案。从 web/ui/src/components/MetaPanel.vue:17-27 原样搬过来
// （BASELINE_BEHAVIOR.md §3.4 要求两份 label 表都跟着走）。
// text_edited 只存在于详情的 trail —— reader.py:610 把账本里的 edited 改写成它。
export const ACTION_LABEL: Record<TrailAction, string> = {
  approved: '通过',
  skipped: '标记为不发',
  snoozed: '暂时挂起',
  woke: '恢复审校',
  handed_off: '交由人工处理',
  handoff_link: '回填手工发布链接',
  scheduled: '确认已排期',
  submit_failed: '提交失败，恢复审校',
  text_edited: '修改了德语译文',
}
