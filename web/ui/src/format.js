// 展示口径。**只管怎么显示，不含任何判断**——判断全在后端。

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

// 排期时刻带的是柏林偏移（+02:00 / +01:00）。**不能交给浏览器的本地时区去渲染**：
// 这台机器在中国，直接 toLocaleString 会把 10:00 柏林显示成 16:00，
// 而那正是这个项目最不能出错的一类数（夏令时切换日尤其）。
// 所以按字符串里带的偏移自己算，显示成柏林当地时刻。
export function formatSchedule(iso) {
  if (!iso) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return null
  const [, y, mo, d, h, mi] = match
  const weekday = WEEKDAYS[new Date(Date.UTC(+y, +mo - 1, +d)).getUTCDay()]
  return `${+mo}/${+d} ${weekday} ${h}:${mi} 柏林`
}

export function formatDate(iso) {
  if (!iso) return '—'
  return String(iso).slice(0, 10)
}

export function formatTrailTime(iso) {
  if (!iso) return ''
  const value = new Date(iso)
  if (Number.isNaN(value.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).format(value)
}
export function formatWakeAt(iso) { return iso ? `${formatTrailTime(iso)} 上海` : '' }

export const PLATFORM_LABEL = { facebook: 'Facebook', instagram: 'Instagram' }

// 业务视角四态 + not_ready（第 7 节）。
// 状态文字不上列表行——视觉区分（已通过的行变灰）比多一列文字省地方；
// 这张表给详情页和筛选器用。
export const STATUS_LABEL = {
  not_ready: '待处理',
  pending_review: '待我审',
  edited: '已修改',
  snoozed: '已挂起',
  approved: '已通过',
  scheduled: '已排期',
  skipped: '这篇不发',
  handed_off: '已交人工处理'
}

export const AUTHOR_KIND_LABEL = {
  own: '原创',
  collab: '合作帖',
  third_party: '第三方作者'
}

// 风险三类（第 8 节）。金额/单位/标签由 regex 负责，不进这里。
export const RISK_KIND_LABEL = {
  pun: '俚语双关',
  ambiguous: '歧义句',
  us_only: '美国限定'
}
