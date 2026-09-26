import type { TaskDetail } from '@/types/domain'
import type { DetailStepId } from '@/app/search-params'

export type StepStatus = 'complete' | 'pending' | 'not_required' | 'view_only'
export interface ReviewTodo { readonly step: DetailStepId; readonly text: string }
export interface ReviewStep {
  readonly id: DetailStepId
  readonly label: string
  readonly status: StepStatus
  readonly todos: readonly ReviewTodo[]
}

const FINISHED = new Set(['approved', 'scheduled', 'skipped', 'handed_off'])

function textTodos(detail: TaskDetail): ReviewTodo[] {
  const todos: ReviewTodo[] = []
  if (!detail.localization.body_de.trim()) todos.push({ step: 'text', text: '补充德语正文' })
  if (detail.text.stale || detail.localization.source_stale) todos.push({ step: 'text', text: '原帖正文已更新，请重新复核德语正文' })
  if (detail.localization.body_de.trim() && !detail.content_review.body.confirmed) todos.push({ step: 'text', text: '确认当前德语正文' })
  for (const issue of detail.localization_validation.issues) {
    if (['body_urls', 'body_hashtags', 'unknown_link_placeholder', 'placeholder_not_supported'].includes(issue.code)) {
      todos.push({ step: 'text', text: issue.message })
    }
  }
  return todos
}

function imageTodos(detail: TaskDetail): ReviewTodo[] {
  const todos: ReviewTodo[] = []
  for (const image of detail.images) {
    const number = image.index + 1
    if (!image.source_image_sha256) todos.push({ step: 'images', text: `第 ${number} 张原图无法读取，请核对归档素材` })
    else if (!image.ready) todos.push({ step: 'images', text: `第 ${number} 张图片请选择拟发布版本` })
    else if (!detail.content_review.images.find(item => item.index === image.index)?.confirmed) {
      todos.push({ step: 'images', text: `确认第 ${number} 张图片用于发布` })
    }
  }
  return todos
}

function localizationTodos(detail: TaskDetail): ReviewTodo[] {
  const { localization: draft } = detail
  const todos: ReviewTodo[] = []
  if ((draft.source_tags.length > 0 || draft.tags.length > 0) && !draft.hashtags_confirmed) {
    todos.push({ step: 'localization', text: '确认本篇拟发布标签' })
  }
  if (detail.platform === 'facebook') {
    for (const [index, link] of draft.links.entries()) {
      if (!link.target_url) todos.push({ step: 'localization', text: `为链接 ${index + 1} 填写德语落地页` })
      else if (!link.confirmed) todos.push({ step: 'localization', text: `确认链接 ${index + 1} 的德语落地页` })
    }
  }
  if ((draft.links.length > 0 || draft.ig_cta) && !draft.links_confirmed
      && !todos.some(item => item.text.includes('链接 '))) {
    todos.push({ step: 'localization', text: '确认本篇链接与主页引导' })
  }
  return todos
}

/** 四步状态只从服务端持久决定和当前内容推导，不把「已查看」当作完成。 */
export function deriveSteps(detail: TaskDetail): ReviewStep[] {
  const finished = FINISHED.has(detail.status)
  const paused = detail.status === 'snoozed'
  const locked = detail.status === 'content_locked'
  const blockedActions = finished || paused || locked || detail.read_only
  const raw = [
    { id: 'text' as const, label: '德语正文', todos: textTodos(detail) },
    { id: 'images' as const, label: '逐张图片', todos: imageTodos(detail) },
    { id: 'localization' as const, label: '标签与链接', todos: localizationTodos(detail) },
  ]
  const steps: ReviewStep[] = raw.map(step => ({
    ...step,
    status: step.id === 'images' && detail.images.length === 0 ? 'not_required'
      : step.todos.length === 0 ? 'complete' : blockedActions ? 'view_only' : 'pending',
    todos: blockedActions ? [] : step.todos,
  }))
  const allReady = steps.every(step => step.status === 'complete' || step.status === 'not_required')
  const finalTodos: ReviewTodo[] = locked ? [{ step: 'final', text: '选择排期时间' }]
    : !blockedActions && allReady ? [{ step: 'final', text: '核对最终文案与图片后冻结内容' }] : []
  steps.push({ id: 'final', label: '最终确认与排期',
    status: detail.status === 'scheduled' ? 'complete' : finished || paused || detail.read_only ? 'view_only' : 'pending',
    todos: finalTodos })
  return steps
}

export function defaultStep(detail: TaskDetail): DetailStepId {
  if (FINISHED.has(detail.status) || detail.status === 'content_locked' || detail.status === 'snoozed') return 'final'
  return deriveSteps(detail).find(step => step.status === 'pending')?.id ?? 'final'
}
