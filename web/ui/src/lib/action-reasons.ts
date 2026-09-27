/** 禁用原因按分支顺序取最高优先级。 */

import type { ContentJob, DisplayStatus } from '@/types/domain'

export interface ApprovalGate {
  readonly editing: boolean
  readonly busy: boolean
  readonly fetching: boolean
  readonly eligible: boolean
  readonly status: DisplayStatus
  readonly optionsFailed: boolean
  /** 后端说这篇现在可以排期。读不到 options 时按 false。 */
  readonly available: boolean
  /** 业务时区墙上时刻，`datetime-local` 的原串。 */
  readonly when: string
}

/** 验证输入完整且日历日期有效；不按浏览器时区转换，夏令时由后端核对。 */
export function isCompleteScheduleTime(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value)
  if (!match) return false
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3])
  const hour = Number(match[4]), minute = Number(match[5])
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59) return false
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return day >= 1 && day <= (days[month - 1] ?? 0)
}

export function approvalDisabledReason(gate: ApprovalGate): string {
  if (gate.editing) return '请先保存或放弃正在编辑的文案'
  if (gate.busy) return '正在提交并核验，请等待'
  if (gate.fetching) return '正在核对发布条件'
  if (!gate.eligible) return gate.status === 'scheduled' ? '这篇已有已确认的排期'
    : ['pending_review', 'edited'].includes(gate.status) ? '请先点「编辑确认无误」冻结内容'
    : '请先恢复审校并准备好内容'
  if (gate.optionsFailed) return '发布条件读取失败，请重新核对'
  if (!gate.available) return '发布条件尚未满足，请查看下方提示'
  if (!gate.when) return '请先填写发布时间'
  if (!isCompleteScheduleTime(gate.when)) return '请填写完整有效的日期和时间'
  return ''
}

/** 服务端的冻结/选时阻断原因可能带命令、路径或录证术语，页面只给业务下一步。 */
const BLOCK_NOTICES: readonly (readonly [RegExp, string])[] = [
  [/金额/, '德语正文里的金额与原帖不一致，请回到德语正文核对金额写法。'],
  [/缺少德语图/, '仍有图片没有可发布的版本，请回到逐张图片处理。'],
  [/没有译文|译文缺失或为空/, '尚无德语正文，请先在德语正文步骤补充。'],
  [/源帖已变更|指纹已过期|提示词版本已过期/, '原帖或德语初稿已更新，请回到德语正文重新复核并保存。'],
  [/平台文案尚未确认/, '标签或链接尚未确认，请回到标签与链接步骤处理。'],
  [/owner|coauthors|归属/, '原帖作者信息不完整，暂不能发布，请联系维护人员核对。'],
]

/** 英文标识、路径、命令参数、长编号或内部代号都算技术痕迹；平台名不算。 */
function technical(text: string): boolean {
  const words = text.replace(/Facebook|Instagram|Business Suite|Meta/g, '')
  return /[A-Za-z_]{3,}|[\\/{}<>=]|--|\d{5,}|G\d/.test(words)
}

/** 后端原因不带技术痕迹时原样给业务看，否则换成同样指向下一步的通用说明。 */
export function businessNotice(message: string | null | undefined, fallback: string): string {
  const text = (message ?? '').trim()
  return text && !technical(text) ? text : fallback
}

/** 保存或确认失败：“先编辑德语”“标签格式”这类能照做的提示要给人看，只说“请重试”会让人一直重试。 */
export function failureNotice(cause: unknown, fallback = '请重试；仍不行请刷新页面后核对。'): string {
  return businessNotice(cause instanceof Error ? cause.message : '', fallback)
}

export function approvalBlockNotice(reason: string | null | undefined): string {
  const text = (reason ?? '').trim()
  if (!text) return ''
  const known = BLOCK_NOTICES.find(([pattern]) => pattern.test(text))
  if (known) return known[1]
  // 服务端已写成业务话术的原因（缺确认、需解除冻结、本月无可选时间）原样显示。
  const fallback = '发布条件暂未满足，请刷新核对；仍无法继续时请联系维护人员。'
  return /^(请|本月|这份冻结|这篇)/.test(text) ? businessNotice(text, fallback) : fallback
}

/** 只读核对回执的结果；读取失败的原文可能带浏览器细节，只在干净时显示。 */
export function receiptCheckNotice(receipt: { readonly status: string; readonly message?: string; readonly projection_error?: string }): string {
  if (receipt.status !== 'scheduled') {
    return businessNotice(receipt.message, '还没读到这次提交对应的后台排期，本地继续保留待核对状态；请稍后再核对，不要重新提交。')
  }
  return receipt.projection_error ? businessNotice(receipt.projection_error, '排期已确认，本地记录待补齐；可以再次核对，不要重新提交。') : ''
}

function contentJobCommon(gate: { readonly editing: boolean; readonly busy: boolean }): string {
  if (gate.editing) return '请先保存或放弃当前编辑'
  if (gate.busy) return '正在处理，请等待'
  return ''
}

export interface InitialTranslationGate {
  readonly editing: boolean
  readonly busy: boolean
  readonly running: boolean
  readonly interrupted: boolean
  readonly available: boolean
  readonly consented: boolean
}

export function initialTranslationDisabledReason(gate: InitialTranslationGate): string {
  const common = contentJobCommon(gate)
  if (common) return common
  if (gate.running) return '当前初翻仍在处理'
  if (gate.interrupted) return '请先核对中断的处理'
  if (!gate.available) return '当前无法开始初翻，请刷新处理状态'
  if (!gate.consented) return '请先确认可以处理这篇第三方内容'
  return ''
}

export interface RefinementGate {
  readonly editing: boolean
  readonly busy: boolean
  readonly eligible: boolean
  readonly running: boolean
  readonly interrupted: boolean
  readonly instruction: string
  readonly capabilitiesLoaded: boolean
  readonly kind: 'text' | 'image'
  readonly remaining: number
  /** 选中的这一张当前是人工图。 */
  readonly manualImage?: boolean
  readonly originalConfirmed?: boolean
}

export function refinementDisabledReason(gate: RefinementGate): string {
  const common = contentJobCommon(gate)
  if (common) return common
  if (!gate.eligible) return '请先恢复审校并复核原文变化'
  if (gate.running) return '当前优化仍在生成'
  if (gate.interrupted) return '请先核对中断的处理'
  if (!gate.instruction.trim()) return '请填写本次希望怎样调整'
  if (!gate.capabilitiesLoaded) return '正在读取可用次数'
  // 人工图优先于程序产出，所以模型再生成一版也不会被采用——那笔钱是白花的。
  if (gate.kind === 'image' && gate.manualImage) return '这一张已换成人工图片，模型优化不会被采用'
  if (gate.kind === 'image' && gate.originalConfirmed) return '这一张已确认使用原图，需要出图时请先撤销原图确认'
  if (gate.kind === 'image' && !gate.remaining) return '这张图片的优化次数已用完'
  return ''
}

export function textCandidateDisabledReason(job: ContentJob, sourceHash: string, eligible: boolean): string {
  if (!eligible) return '请先恢复审校并复核原文变化'
  if (job.source_text_sha256 !== sourceHash) return '原文已更新，此候选已失效。'
  if (job.prompt_current !== true) return '提示词已更新或任务缺少版本依据，此候选已失效。'
  if (job.body_de === undefined) return '正在读取候选正文，请稍后再采用。'
  return ''
}

/** 仅未触碰且为空时预填；人工清空仍算已触碰，切换任务后重置。 */
export function seedScheduleTime(state: {
  readonly current: string
  readonly touched: boolean
  readonly earliest: string | null | undefined
  readonly scheduledAt: string | null | undefined
}): string | null {
  if (state.touched || state.current || !state.earliest) return null
  return state.scheduledAt || state.earliest
}
