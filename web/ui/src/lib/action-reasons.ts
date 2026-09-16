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
  /** 柏林墙上时刻，`datetime-local` 的原串。 */
  readonly when: string
}

/** 验证输入完整且日历日期有效；不按浏览器时区转换，柏林夏令时由后端核对。 */
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
  if (!gate.eligible) return gate.status === 'scheduled' ? '这篇已有已确认的排期' : '请先恢复审校并准备好内容'
  if (gate.optionsFailed) return '发布条件读取失败，请重新核对'
  if (!gate.available) return '发布条件尚未满足，请查看下方提示'
  if (!gate.when) return '请先填写柏林发布时间'
  if (!isCompleteScheduleTime(gate.when)) return '请填写完整有效的柏林日期和时间'
  return ''
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
}

export function refinementDisabledReason(gate: RefinementGate): string {
  const common = contentJobCommon(gate)
  if (common) return common
  if (!gate.eligible) return '请先恢复审校并复核原文变化'
  if (gate.running) return '当前优化仍在生成'
  if (gate.interrupted) return '请先核对中断的处理'
  if (!gate.instruction.trim()) return '请填写本次希望怎样调整'
  if (!gate.capabilitiesLoaded) return '正在读取可用次数与费用'
  // 人工图优先于程序产出，所以模型再生成一版也不会被采用——那笔钱是白花的。
  if (gate.kind === 'image' && gate.manualImage) return '这一张已换成人工图片，模型优化不会被采用'
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
