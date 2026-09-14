import type { ApprovalOptions, ApproveReceipt, TaskDetail } from '@/types/domain'
import { idPath, jsonBody, request } from './http'
export const approvalOptions = (id: string) => request<ApprovalOptions>(`/api/tasks/${idPath(id)}/approval-options`)
export const approvalBody = (detail: TaskDetail, scheduled_at: string, options: ApprovalOptions) => ({ scheduled_at,
  source_text_sha256: detail.text.source_text_sha256, human_revision: detail.text.human_revision,
  review_revision: detail.review.revision, content_fingerprint: options.fingerprint })
export type ApprovalBody = ReturnType<typeof approvalBody>
export const approve = (id: string, body: ApprovalBody) => request<ApproveReceipt>(`/api/tasks/${idPath(id)}/approve`, jsonBody(body))
export const reconcilePublication = (id: string) => request<unknown>(`/api/tasks/${idPath(id)}/publication/reconcile`, jsonBody({}))
export const isScheduledReceipt = (receipt: ApproveReceipt) => receipt.ok === true && receipt.status === 'scheduled'
