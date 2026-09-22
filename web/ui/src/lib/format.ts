
import type {
  AuthorKind,
  DisplayStatus,
  Platform,
  RiskKind,
  TrailAction,
} from '@/types/domain'

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const

/** 业务时区由后端 `business_timezone` 给出；这里只在它缺失时兜底，不另做决定。 */
export const BUSINESS_TIMEZONE = 'Asia/Shanghai'
/** 德国受众所在时区。业务时刻换算过去才是粉丝看到帖子的钟点。 */
export const AUDIENCE_TIMEZONE = 'Europe/Berlin'

/** 仅把后端有偏移的时刻转换成某个时区的输入值；用户输入保持原字符串，由服务端判定 DST。 */
export function zonedInput(iso: string | null | undefined, zone: string = BUSINESS_TIMEZONE): string {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(value).map(part => [part.type, part.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

/** 比较两条业务墙上时刻的缓存间隔，不负责判断 DST 合法性。 */
export const wallMinutesApart = (a: string, b: string) => Math.abs(Date.parse(a.slice(0, 16) + ':00Z') - Date.parse(b.slice(0, 16) + ':00Z')) / 60000

/** 按业务时区计算今天，避免跨日边界受宿主时区影响。 */
export const businessToday = (zone: string = BUSINESS_TIMEZONE, now: Date = new Date()) =>
  zonedInput(now.toISOString(), zone).slice(0, 10)

/**
 * 业务时刻在德国是几点。北京 16:00 是柏林 10:00，北京 10:00 却是柏林凌晨 4 点——
 * 选时刻的人看的是北京，看帖子的人在德国，这一行不显示出来就只能靠记时差。
 */
export function audienceHint(wallClock: string, businessZone: string = BUSINESS_TIMEZONE): { text: string; quiet: boolean } | null {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(wallClock)) return null
  // 用两个时区在同一瞬间的偏移差把墙上时刻搬过去，避免依赖宿主时区。
  const probe = new Date(wallClock + ':00Z')
  if (Number.isNaN(probe.getTime())) return null
  const offset = (zone: string) => Date.parse(zonedInput(probe.toISOString(), zone) + ':00Z') - probe.getTime()
  const shifted = new Date(probe.getTime() - offset(businessZone) + offset(AUDIENCE_TIMEZONE))
  const local = zonedInput(shifted.toISOString(), 'UTC')
  if (!local) return null
  const hour = Number(local.slice(11, 13))
  return { text: `${+local.slice(5, 7)}/${+local.slice(8, 10)} ${local.slice(11, 16)} 柏林`, quiet: hour < 6 }
}

/** 月历只迭代日期标签；跨月边界和每张卡的柏林日期均来自后端。 */
export function calendarDays(start: string, end: string): (string | null)[] {
  const first = new Date(start.slice(0, 10) + 'T00:00:00Z'), last = new Date(end.slice(0, 10) + 'T00:00:00Z')
  if (!Number.isFinite(first.getTime()) || !Number.isFinite(last.getTime())) return []
  const days: (string | null)[] = Array.from({ length: (first.getUTCDay() + 6) % 7 }, () => null)
  for (let stamp = first.getTime(); stamp <= last.getTime(); stamp += 86400000) days.push(new Date(stamp).toISOString().slice(0, 10))
  return days
}

// 显示字符串携带的业务墙上时刻（当前是北京），不转换为浏览器本地时区。
export function formatSchedule(iso: string | null | undefined): string | null {
  if (!iso) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return null
  const [, y, mo, d, h, mi] = match
  if (y === undefined || mo === undefined || d === undefined || h === undefined || mi === undefined) {
    return null
  }
  const weekday = WEEKDAYS[new Date(Date.UTC(+y, +mo - 1, +d)).getUTCDay()] ?? ''
  return `${+mo}/${+d} ${weekday} ${h}:${mi} 北京`
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

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  not_ready: '未就绪',
  pending_review: '待我审',
  edited: '已修改',
  content_locked: '内容已冻结',
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

export const RISK_KIND_LABEL: Record<RiskKind, string> = {
  pun: '俚语双关',
  ambiguous: '歧义句',
  us_only: '美国限定',
}

// 详情 trail 将账本的 edited 映射为 text_edited。
export const ACTION_LABEL: Record<TrailAction, string> = {
  approved: '通过',
  skipped: '标记为不发',
  snoozed: '暂时挂起',
  woke: '恢复审校',
  handed_off: '交由人工处理',
  handoff_link: '回填手工发布链接',
  scheduled: '确认已排期',
  submit_failed: '提交失败，恢复审校',
  unscheduled: '登记已在后台删除排期',
  content_locked: '确认内容无误并冻结',
  unlocked: '解除冻结',
  text_edited: '修改了德语译文',
  image_selected: '更新了图片选择',
}
