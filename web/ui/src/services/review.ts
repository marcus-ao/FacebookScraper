import type { ReviewActionRequest, ReviewListItem, TaskDetail } from '@/types/domain'
import { idPath, jsonBody, request, requestFile, triggerDownload } from './http'

export type ReviewContext = ReviewListItem | TaskDetail
export type DecisionAction = ReviewActionRequest | 'export'
export interface DecisionForm {
  action: DecisionAction
  reason: string
  wakeAt: string
  handoffUrl: string
}

export function decisionBody(context: ReviewContext, form: DecisionForm) {
  return {
    source_text_sha256: 'text' in context ? context.text.source_text_sha256 : context.source_text_sha256,
    review_revision: context.review.revision ?? null,
    action: form.action,
    reason: form.reason,
    handoff_url: form.handoffUrl,
    ...(form.wakeAt ? { wake_at: `${form.wakeAt}:00+08:00` } : {}),
  }
}

export async function applyDecision(context: ReviewContext, form: DecisionForm): Promise<TaskDetail> {
  const path = `/api/tasks/${idPath(context.id)}`
  const body = jsonBody(decisionBody(context, form))
  if (form.action === 'export') {
    triggerDownload(await requestFile(`${path}/export`,
      jsonBody({ ...decisionBody(context, form), mode: 'handoff' }), 'post_de.zip'))
    return request<TaskDetail>(path)
  }
  return request<TaskDetail>(`${path}/review`, body)
}

export async function downloadPost(detail: TaskDetail): Promise<void> {
  triggerDownload(await requestFile(`/api/tasks/${idPath(detail.id)}/export`,
    jsonBody({ mode: 'download', source_text_sha256: detail.text.source_text_sha256,
      review_revision: detail.review.revision ?? null }), 'post_de.zip'))
}
