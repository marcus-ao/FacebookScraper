/**
 * 「为什么现在不能点」的判定。**纯函数，没有 React 依赖**，所以能单测。
 *
 * 这几句话是运营唯一能拿到的解释 —— 顺序排错了，她会先去做一件不该先做的事。
 * 所以每条 `if` 就是一级优先级，从「她自己动一下手就能解决的」排到
 * 「只能等系统的」：先说正在编辑，再说正在提交，最后才说条件不满足。
 *
 * ⛔ 不要把它们改回嵌套三元。原来 useApproval 里那一行是七层，
 *    ContentJobs 里两行各五、六层，读的人没法确认哪一条先赢。
 * ⛔ 也不要做成通用规则引擎。这里只有三个动作，多一层抽象就要多读一层。
 */

import type { DisplayStatus } from '@/types/domain'

export interface ApprovalGate {
  readonly editing: boolean
  readonly busy: boolean
  /** approval-options 正在读。 */
  readonly fetching: boolean
  /** 状态允许通过（pending_review / edited）。 */
  readonly eligible: boolean
  readonly status: DisplayStatus
  readonly optionsFailed: boolean
  /** 后端说这篇现在可以排期。读不到 options 时按 false。 */
  readonly available: boolean
  /** 柏林墙上时刻，`datetime-local` 的原串。 */
  readonly when: string
}

export function approvalDisabledReason(gate: ApprovalGate): string {
  if (gate.editing) return '请先保存或放弃正在编辑的文案'
  if (gate.busy) return '正在提交并核验，请等待'
  if (gate.fetching) return '正在核对发布条件'
  if (!gate.eligible) return gate.status === 'scheduled' ? '这篇已有已确认的排期' : '请先恢复审校并准备好内容'
  if (gate.optionsFailed) return '发布条件读取失败，请重新核对'
  if (!gate.available) return '发布条件尚未满足，请查看下方提示'
  if (!gate.when) return '请先填写柏林发布时间'
  return ''
}

/** 初翻与单篇优化共用的前两条：都要先把编辑区腾出来。 */
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
  /** 第三方作者的处理授权。 */
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
  /** 可用次数与费用已经读到。 */
  readonly capabilitiesLoaded: boolean
  readonly kind: 'text' | 'image'
  readonly remaining: number
}

export function refinementDisabledReason(gate: RefinementGate): string {
  const common = contentJobCommon(gate)
  if (common) return common
  if (!gate.eligible) return '请先恢复审校并复核原文变化'
  if (gate.running) return '当前优化仍在生成'
  if (gate.interrupted) return '请先核对中断的处理'
  if (!gate.instruction.trim()) return '请填写本次希望怎样调整'
  if (!gate.capabilitiesLoaded) return '正在读取可用次数与费用'
  if (gate.kind === 'image' && !gate.remaining) return '这张图片的优化次数已用完'
  return ''
}

/**
 * 默认排期时刻要不要填进去。
 *
 * 只有三件事同时成立才填：**没人碰过这个字段**、当前是空的、后端给了可选范围。
 * 她把时间清掉是一个决定 —— 下一次 approval-options 回来又替她填回去，
 * 等于和人抢输入框，而这个字段决定的是帖子什么时候公开发出去。
 * 换一篇任务时 `touched` 重置，新的一篇照样有默认值。
 */
export function seedScheduleTime(state: {
  readonly current: string
  readonly touched: boolean
  readonly earliest: string | null | undefined
  readonly scheduledAt: string | null | undefined
}): string | null {
  if (state.touched || state.current || !state.earliest) return null
  return state.scheduledAt || state.earliest
}
